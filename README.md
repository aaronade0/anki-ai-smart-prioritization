# AI Smart Prioritization for Anki

This add-on uses OpenAI (e.g., GPT-4o or GPT-5-mini) to intelligently analyze your Anki cards and assign them a priority tag (`prio:1` to `prio:4`) based on their importance relative to the deck topic.

## 🚀 Features

* **Context-Aware:** Analyzes cards in batches within the context of their Deck names (e.g., "Physics::Electricity").
* **Custom Instructions:** You can give the AI specific focus (e.g., "Focus on definitions relevant for High School finals").
* **Smart Skipping:** Option to skip cards that are already prioritized.
* **4 Priority Levels:**
    * `prio:1` (High): Core concepts, exam-relevant.
    * `prio:2` (Medium): Important details.
    * `prio:3` (Low): Nice to know.
    * `prio:4` (Unnecessary): Trivia/Redundant.

## 🛠 Setup

1.  Install the add-on.
2.  Go to **Tools** -> **Add-ons**.
3.  Select **AI Smart Prioritization** and click **Config**.
4.  Paste your OpenAI API Key into the `"api_key"` field.
5.  (Optional) Change the model (default: `gpt-5-mini`) or batch size.

## 🎓 How to Study by Priority

The main purpose of this add-on is to let you focus on what matters most. Anki does not use tags for scheduling by default, so you must use **Filtered Decks**.

### Step 1: Prioritize your cards
Go to **Tools** -> **AI Prioritization**, select your deck, and let the AI run.

### Step 2: Create a Study Session
1.  On the Anki main screen, press `F` (or click Tools -> Create Filtered Deck).
2.  In the "Search" field, enter the tag you want to study.
    * To study **only the most important cards**:
        `deck:"YourDeckName" tag:prio:1 is:due`
    * To study **High and Medium** importance:
        `deck:"YourDeckName" (tag:prio:1 OR tag:prio:2) is:due`
3.  Click **Build**.
4.  Study this deck! When you are done or want to update, click "Rebuild" at the bottom.

*Tip: You can delete cards tagged with `prio:4` if you want to clean up your collection.*

## ⚠️ Disclaimer
This add-on requires an internet connection and a paid OpenAI API key. Usage costs apply (usually very low for text).
