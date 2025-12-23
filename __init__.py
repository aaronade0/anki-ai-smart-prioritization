import json
import urllib.request
import urllib.error
import re
import xml.etree.ElementTree as ET

from aqt import mw
from aqt.utils import showInfo
from aqt.qt import *

# --- STANDARD CONFIG ---
DEFAULT_PROVIDER = "openai" # "openai" or "gemini"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_GEMINI_MODEL = "gemini-1.5-flash"
DEFAULT_BATCH_SIZE = 10
MIN_CONTEXT_SIZE = 10
DEBUG_LOG = False

# ----------------- CONFIG MANAGER -----------------

def get_config():
    """Loads config from Anki."""
    config = mw.addonManager.getConfig(__name__)
    if not config:
        config = {
            "provider": DEFAULT_PROVIDER,
            "openai_api_key": "",
            "gemini_api_key": "",
            "openai_model": DEFAULT_OPENAI_MODEL,
            "gemini_model": DEFAULT_GEMINI_MODEL,
            "batch_size": DEFAULT_BATCH_SIZE
        }
    return config

def get_active_api_key(provider):
    """Retrieves the key for the active provider."""
    config = get_config()
    if provider == "gemini":
        key = config.get("gemini_api_key", "").strip()
        label = "Google Gemini"
    else:
        key = config.get("openai_api_key", "").strip()
        label = "OpenAI"

    if not key:
        showInfo(
            f"{label} API Key missing.\n\n"
            "Please go to: Tools -> Add-ons -> AI Prioritization -> Config\n"
            f"and paste your {label} API Key."
        )
        return None
    return key

# ----------------- HELPER FUNCTIONS -----------------

def strip_html(text: str) -> str:
    text = text.replace("<div>", " ").replace("<br>", " ").replace("&nbsp;", " ")
    try:
        if "<" in text:
            root = ET.fromstring(f"<span>{text}</span>")
            return "".join(root.itertext())
    except Exception:
        pass
    return text

def clean_json_response(text):
    """Removes markdown code blocks if present."""
    text = text.strip()
    # Remove ```json ... ``` wrapper
    text = re.sub(r"^```json\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"```$", "", text, flags=re.MULTILINE)
    return text.strip()

def note_has_prio_tag(note) -> bool:
    return any(note.has_tag(t) for t in ["prio:1", "prio:2", "prio:3", "prio:4"])

def get_deck_name_for_note(nid: int) -> str:
    cids = mw.col.find_cards(f"nid:{nid}")
    if cids:
        card = mw.col.get_card(cids[0])
        did = card.did
    else:
        did = mw.col.decks.current()["id"]
    return mw.col.decks.name(did)

def group_notes_by_deck_hierarchy(nids):
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
        mw, "Choose Deck", "Prioritize which deck?\n(Subdecks included)",
        sorted(all_decks), 0, False,
    )
    if not ok or not deck_name:
        return None
    return deck_name

# ----------------- UI DIALOG -----------------

class ConfigDialog(QDialog):
    def __init__(self, parent, total_count, prio_count):
        super().__init__(parent)
        self.setWindowTitle("Prioritization Settings")
        self.setMinimumWidth(400)
        layout = QVBoxLayout()
        
        info_text = (
            f"<b>Found {total_count} cards.</b><br>"
            f"{prio_count} cards already have priority tags."
        )
        layout.addWidget(QLabel(info_text))
        layout.addSpacing(10)
        
        self.group = QButtonGroup(self)
        self.rb_skip = QRadioButton(f"Skip existing")
        self.rb_skip.setChecked(True)
        self.group.addButton(self.rb_skip)
        layout.addWidget(self.rb_skip)
        
        self.rb_overwrite = QRadioButton("Reprioritize ALL")
        self.group.addButton(self.rb_overwrite)
        layout.addWidget(self.rb_overwrite)
        
        layout.addSpacing(15)
        layout.addWidget(QLabel("Custom Context (Optional):"))
        self.prompt_input = QLineEdit()
        self.prompt_input.setPlaceholderText("e.g. 'Focus on Math Abitur'")
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

# ----------------- PROMPT GENERATOR -----------------

def get_system_prompt(deck_context_name, custom_user_instruction=""):
    custom_text = f"\nUSER FOCUS: {custom_user_instruction}\n" if custom_user_instruction else ""
    return f"""
You are a strict teacher for: '{deck_context_name}'.
{custom_text}
Task: Prioritize these flashcards RELATIVE to each other.
Priorities (1-4):
1 (HIGH): Core concepts, exam-relevant.
2 (MEDIUM): Important details.
3 (LOW): Niche, "nice to know".
4 (UNNECESSARY): Trivia/Redundant.

Target distribution: 15% Prio 1, 40% Prio 2, 30% Prio 3, 15% Prio 4.

Reply ONLY with valid JSON (no markdown):
{{"ratings": [{{"id": 123456, "prio": 1}}, {{"id": 234567, "prio": 4}}]}}
"""

def prepare_user_message(cards_data):
    lines = []
    for c in cards_data:
        q = (c["question"][:200] + "..") if len(c["question"]) > 200 else c["question"]
        a = (c["answer"][:200] + "..") if len(c["answer"]) > 200 else c["answer"]
        lines.append(f"ID: {c['id']} | Q: {q} | A: {a}")
    return "\n".join(lines)

# ----------------- API CALLS -----------------

def call_openai(cards_data, deck_name, api_key, model, custom_prompt):
    url = "[https://api.openai.com/v1/chat/completions](https://api.openai.com/v1/chat/completions)"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": get_system_prompt(deck_name, custom_prompt)},
            {"role": "user", "content": prepare_user_message(cards_data)},
        ],
        "response_format": {"type": "json_object"},
    }
    
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    )
    
    with urllib.request.urlopen(req, timeout=120) as response:
        res = json.load(response)
        return json.loads(res["choices"][0]["message"]["content"])

def call_gemini(cards_data, deck_name, api_key, model, custom_prompt):
    # Gemini REST API URL
    url = f"[https://generativelanguage.googleapis.com/v1beta/models/](https://generativelanguage.googleapis.com/v1beta/models/){model}:generateContent?key={api_key}"
    
    # Gemini Logic: System prompt goes into specific field or prepended
    sys_prompt = get_system_prompt(deck_name, custom_prompt)
    user_msg = prepare_user_message(cards_data)
    
    payload = {
        "contents": [{
            "parts": [{"text": user_msg}]
        }],
        "systemInstruction": {
            "parts": [{"text": sys_prompt}]
        },
        "generationConfig": {
            "response_mime_type": "application/json"
        }
    }

    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    
    with urllib.request.urlopen(req, timeout=120) as response:
        res = json.load(response)
        text = res["candidates"][0]["content"]["parts"][0]["text"]
        clean_text = clean_json_response(text)
        return json.loads(clean_text)

def call_api_dispatch(provider, cards, deck, key, model, custom):
    try:
        if provider == "gemini":
            return call_gemini(cards, deck, key, model, custom)
        else:
            return call_openai(cards, deck, key, model, custom)
    except urllib.error.URLError as e:
        print(f"API Error: {e}")
        return None
    except Exception as e:
        print(f"General Error: {e}")
        return None

# ----------------- APPLY PRIO -----------------

def apply_priority(ratings):
    cnt = 0
    if not ratings or "ratings" not in ratings: return 0
    
    for item in ratings["ratings"]:
        try:
            nid = item["id"]
            prio = item["prio"]
            if prio not in (1, 2, 3, 4): continue

            note = mw.col.get_note(nid)
            for t in ["prio:1", "prio:2", "prio:3", "prio:4"]:
                if note.has_tag(t): note.remove_tag(t)

            note.add_tag(f"prio:{prio}")
            mw.col.update_note(note)
            cnt += 1
        except: continue
    return cnt

# ----------------- MAIN -----------------

def on_prioritize_smart():
    deck_name = choose_deck()
    if not deck_name: return

    config = get_config()
    provider = config.get("provider", "openai").lower()
    
    if provider == "gemini":
        model = config.get("gemini_model", DEFAULT_GEMINI_MODEL)
    else:
        model = config.get("openai_model", DEFAULT_OPENAI_MODEL)

    api_key = get_active_api_key(provider)
    if not api_key: return

    search = f'deck:"{deck_name}"'
    nids = mw.col.find_notes(search)
    if not nids:
        showInfo(f"No cards found.")
        return

    prio_count = sum(1 for nid in nids if note_has_prio_tag(mw.col.get_note(nid)))
    dialog = ConfigDialog(mw, len(nids), prio_count)
    if dialog.exec() != QDialog.DialogCode.Accepted: return

    settings = dialog.get_data()
    skip = settings["skip_existing"]
    custom = settings["custom_prompt"]

    mw.progress.start(label="Initializing...", max=len(nids))
    
    total_cards = len(nids)
    stats = {"seen": 0, "prio": 0, "skipped": 0}
    
    try:
        groups = group_notes_by_deck_hierarchy(nids)
        batch_size = config.get("batch_size", 10)

        for g_name, g_nids in groups.items():
            batch = []
            for nid in g_nids:
                stats["seen"] += 1
                note = mw.col.get_note(nid)
                
                label = f"Progress: {int((stats['seen']/total_cards)*100)}%"

                if skip and note_has_prio_tag(note):
                    stats["skipped"] += 1
                    mw.progress.update(value=stats["seen"], label=f"{label} | Skipping...")
                    continue

                q = strip_html(note.fields[0])
                a = strip_html(note.fields[1]) if len(note.fields) > 1 else ""
                batch.append({"id": nid, "question": q, "answer": a})

                if len(batch) >= batch_size:
                    mw.progress.update(value=stats["seen"], label=f"{label} | Calling {provider}...")
                    mw.app.processEvents()
                    resp = call_api_dispatch(provider, batch, g_name, api_key, model, custom)
                    stats["prio"] += apply_priority(resp)
                    batch = []

            if batch:
                mw.progress.update(value=stats["seen"], label=f"{label} | Finishing...")
                mw.app.processEvents()
                resp = call_api_dispatch(provider, batch, g_name, api_key, model, custom)
                stats["prio"] += apply_priority(resp)

    finally:
        mw.progress.finish()
        mw.reset()

    showInfo(
        f"Done using {provider.title()}!\n"
        f"{stats['prio']} prioritized.\n"
        f"{stats['skipped']} skipped."
    )

action = QAction("AI Prioritization", mw)
action.triggered.connect(on_prioritize_smart)
mw.form.menuTools.addAction(action)