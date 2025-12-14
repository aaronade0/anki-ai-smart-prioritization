import json
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

from aqt import mw
from aqt.utils import showInfo
from aqt.qt import *

# --- STANDARD CONFIG ---
# Diese Werte werden genutzt, wenn der User nichts in der Config ändert
DEFAULT_MODEL = "gpt-4o-mini" # Standardmäßig das günstigere Modell
DEFAULT_BATCH_SIZE = 10
MIN_CONTEXT_SIZE = 10
DEBUG_LOG = False

# ----------------- CONFIG MANAGER -----------------

def get_config():
    """Lädt die Konfiguration aus Ankis Addon-Manager."""
    config = mw.addonManager.getConfig(__name__)
    if not config:
        config = {
            "api_key": "",
            "model": DEFAULT_MODEL,
            "batch_size": DEFAULT_BATCH_SIZE
        }
    return config

def get_api_key():
    """Liest den API-Key aus der Konfiguration."""
    config = get_config()
    key = config.get("api_key", "").strip()
    if not key or "sk-" not in key:
        showInfo(
            "API Key missing or invalid.\n\n"
            "Please go to: Tools -> Add-ons -> AI Prioritization -> Config\n"
            "and paste your OpenAI API Key there."
        )
        return None
    return key

# ----------------- HELPER FUNCTIONS -----------------

def strip_html(text: str) -> str:
    """Basic HTML stripping to save tokens."""
    text = text.replace("<div>", " ").replace("<br>", " ").replace("&nbsp;", " ")
    try:
        if "<" in text:
            root = ET.fromstring(f"<span>{text}</span>")
            return "".join(root.itertext())
    except Exception:
        pass
    return text

def note_has_prio_tag(note) -> bool:
    """Returns True if note has any prio:1 to prio:4 tag."""
    return any(note.has_tag(t) for t in ["prio:1", "prio:2", "prio:3", "prio:4"])

def get_deck_name_for_note(nid: int) -> str:
    """Determine deck name via the first card of the note."""
    cids = mw.col.find_cards(f"nid:{nid}")
    if cids:
        card = mw.col.get_card(cids[0])
        did = card.did
    else:
        did = mw.col.decks.current()["id"]
    return mw.col.decks.name(did)

def group_notes_by_deck_hierarchy(nids):
    """Groups notes by deck hierarchy."""
    temp_groups = {}
    for nid in nids:
        deck_name = get_deck_name_for_note(nid)
        if deck_name not in temp_groups:
            temp_groups[deck_name] = []
        temp_groups[deck_name].append(nid)

    final_groups = {}
    sorted_deck_names = sorted(
        temp_groups.keys(), key=lambda x: x.count("::"), reverse=True
    )

    for name in sorted_deck_names:
        cards = temp_groups[name]
        if len(cards) >= MIN_CONTEXT_SIZE or "::" not in name:
            final_groups[name] = cards
        else:
            parent_name = "::".join(name.split("::")[:-1])
            while parent_name and parent_name not in temp_groups:
                parent_name = "::".join(parent_name.split("::")[:-1])

            if parent_name:
                temp_groups[parent_name].extend(cards)
            else:
                final_groups[name] = cards

    return final_groups

def choose_deck():
    all_decks = [d["name"] for d in mw.col.decks.all()]
    if not all_decks:
        showInfo("No decks found.")
        return None

    deck_name, ok = QInputDialog.getItem(
        mw,
        "Choose Deck",
        "Which deck should be prioritized?\n(Subdecks included)",
        sorted(all_decks),
        0,
        False,
    )

    if not ok or not deck_name:
        return None
    return deck_name

# ----------------- CUSTOM UI DIALOG -----------------

class ConfigDialog(QDialog):
    def __init__(self, parent, total_count, prio_count):
        super().__init__(parent)
        self.setWindowTitle("Prioritization Settings")
        self.setMinimumWidth(400)
        
        layout = QVBoxLayout()
        
        info_text = (
            f"<b>Found {total_count} cards in selected deck.</b><br>"
            f"{prio_count} cards already have a priority tag (prio:1-4)."
        )
        label = QLabel(info_text)
        layout.addWidget(label)
        layout.addSpacing(10)
        
        self.group = QButtonGroup(self)
        self.rb_skip = QRadioButton(f"Skip existing ({prio_count} cards will be ignored)")
        self.rb_skip.setChecked(True)
        self.group.addButton(self.rb_skip)
        layout.addWidget(self.rb_skip)
        
        self.rb_overwrite = QRadioButton("Reprioritize ALL (Overwrite existing tags)")
        self.group.addButton(self.rb_overwrite)
        layout.addWidget(self.rb_overwrite)
        
        layout.addSpacing(15)
        layout.addWidget(QLabel("Custom Context / Instruction (Optional):"))
        self.prompt_input = QLineEdit()
        self.prompt_input.setPlaceholderText("e.g. 'Focus on Math Abitur BW topics'")
        layout.addWidget(self.prompt_input)
        
        layout.addSpacing(15)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        self.setLayout(layout)

    def get_data(self):
        return {
            "skip_existing": self.rb_skip.isChecked(),
            "custom_prompt": self.prompt_input.text().strip()
        }

# ----------------- OPENAI CALL -----------------

def call_openai_batch(cards_data, deck_context_name, api_key, model, custom_user_instruction=""):
    """Calls OpenAI API."""
    url = "https://api.openai.com/v1/chat/completions"
    
    custom_instruction_text = ""
    if custom_user_instruction:
        custom_instruction_text = f"\nIMPORTANT USER INSTRUCTION: {custom_user_instruction}\n"

    system_prompt = f"""
You are a strict teacher for the subject: '{deck_context_name}'.
{custom_instruction_text}
Your task: Prioritize the following flashcards RELATIVE to each other.
Assign priorities (1, 2, 3, or 4):

1 (HIGH): Fundamental basics, core concepts, absolutely relevant for exams.
2 (MEDIUM): Important details, standard knowledge.
3 (LOW): Niche knowledge, "nice to know", rarely asked.
4 (UNNECESSARY): Extreme details, trivia, or redundant facts.

Aim for a distribution like: 15% Prio 1, 40% Prio 2, 30% Prio 3, 15% Prio 4.

Reply ONLY with valid JSON:
{{"ratings": [{{"id": 123456, "prio": 1}}, {{"id": 234567, "prio": 4}}]}}
"""

    cards_text_list = []
    for c in cards_data:
        q = (c["question"][:300] + "..") if len(c["question"]) > 300 else c["question"]
        a = (c["answer"][:300] + "..") if len(c["answer"]) > 300 else c["answer"]
        cards_text_list.append(f"ID: {c['id']} | Q: {q} | A: {a}")

    user_message = "\n".join(cards_text_list)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "response_format": {"type": "json_object"},
    }

    if DEBUG_LOG:
        print(f"=== Sending batch of {len(cards_data)} cards to OpenAI ({model}) ===")

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        # 180s Timeout for Reasoning Models
        with urllib.request.urlopen(req, timeout=180) as response:
            result = json.load(response)
            content = result["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            return parsed
            
    except urllib.error.URLError as e:
        if "timed out" in str(e):
            print(f"ERROR: Timeout at OpenAI.")
        else:
            print(f"API Connection Error: {e}")
            try:
                if hasattr(e, 'read'):
                    print(f"Server Response: {e.read().decode('utf-8')}")
            except: pass
        return None
    except Exception as e:
        print(f"General Error: {e}")
        return None

# ----------------- APPLY PRIO -----------------

def apply_priority(ratings):
    """Sets prio:1/2/3/4 tags."""
    cnt = 0
    for item in ratings:
        try:
            nid = item["id"]
            prio = item["prio"]

            if prio not in (1, 2, 3, 4):
                continue

            note = mw.col.get_note(nid)
            for t in ["prio:1", "prio:2", "prio:3", "prio:4"]:
                if note.has_tag(t):
                    note.remove_tag(t)

            note.add_tag(f"prio:{prio}")
            mw.col.update_note(note)
            cnt += 1
        except Exception as e:
            print(f"Error setting prio for {item}: {e}")
            continue
    return cnt

# ----------------- MAIN FUNCTION -----------------

def on_prioritize_smart():
    deck_name = choose_deck()
    if not deck_name:
        return

    config = get_config()
    model = config.get("model", DEFAULT_MODEL)
    batch_size = config.get("batch_size", DEFAULT_BATCH_SIZE)
    
    # Check API Key before starting
    api_key = get_api_key()
    if not api_key:
        return

    search = f'deck:"{deck_name}"'
    nids = mw.col.find_notes(search)

    if not nids:
        showInfo(f"No cards found in deck '{deck_name}'.")
        return

    prio_count = 0
    for nid in nids:
        note = mw.col.get_note(nid)
        if note_has_prio_tag(note):
            prio_count += 1

    dialog = ConfigDialog(mw, len(nids), prio_count)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return

    user_settings = dialog.get_data()
    skip_existing = user_settings["skip_existing"]
    custom_prompt = user_settings["custom_prompt"]

    mw.progress.start(label="Initializing...", max=len(nids))

    total_cards = len(nids)
    total_seen = 0
    total_prioritized = 0
    total_skipped = 0
    
    try:
        deck_groups = group_notes_by_deck_hierarchy(nids)

        for group_name, group_nids in deck_groups.items():
            current_batch = []

            for nid in group_nids:
                note = mw.col.get_note(nid)
                total_seen += 1
                percent = (total_seen / total_cards) * 100
                progress_label = f"Progress: {percent:.1f}%"

                if skip_existing and note_has_prio_tag(note):
                    total_skipped += 1
                    mw.progress.update(value=total_seen, label=f"{progress_label} | Skipping...")
                    continue

                q = strip_html(note.fields[0])
                a = strip_html(note.fields[1]) if len(note.fields) > 1 else ""
                current_batch.append({"id": nid, "question": q, "answer": a})

                if len(current_batch) >= batch_size:
                    mw.progress.update(value=total_seen, label=f"{progress_label} | Analyzing...")
                    mw.app.processEvents()
                    
                    resp = call_openai_batch(current_batch, group_name, api_key, model, custom_prompt)
                    if resp and "ratings" in resp:
                        applied = apply_priority(resp["ratings"])
                        total_prioritized += applied
                    current_batch = []

            if current_batch:
                mw.progress.update(value=total_seen, label=f"{progress_label} | Finishing...")
                mw.app.processEvents()
                resp = call_openai_batch(current_batch, group_name, api_key, model, custom_prompt)
                if resp and "ratings" in resp:
                    applied = apply_priority(resp["ratings"])
                    total_prioritized += applied

    finally:
        mw.progress.finish()
        mw.reset()

    showInfo(
        f"Done!\n"
        f"Model: {model}\n"
        f"{total_prioritized} newly prioritized.\n"
        f"{total_skipped} skipped."
    )

action = QAction("AI Prioritization", mw)
action.triggered.connect(on_prioritize_smart)
mw.form.menuTools.addAction(action)
