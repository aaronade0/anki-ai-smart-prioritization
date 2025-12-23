# AI Smart Prioritization for Anki

This add-on uses **OpenAI** (GPT-4o/GPT-5) OR **Google Gemini** to intelligently analyze your Anki cards and assign them a priority tag (`prio:1` to `prio:4`).

## 🚀 Features

* **Multi-Provider Support:** Switch between OpenAI and Google Gemini (which has a generous free tier).
* **Context-Aware:** Analyzes cards based on their Deck context (e.g., "Biology::Genetics").
* **Smart Skipping:** Skips cards that already have a priority.
* **4 Priority Levels:**
    * `prio:1` (High): Core concepts, exam-relevant.
    * `prio:2` (Medium): Important details.
    * `prio:3` (Low): Nice to know.
    * `prio:4` (Unnecessary): Trivia/Redundant.

## 🛠 Setup

1.  **Install** the add-on.
2.  Go to **Tools** -> **Add-ons**.
3.  Select **AI Smart Prioritization** and click **Config**.
4.  **Choose your Provider:**
    * Set `"provider"` to either `"openai"` or `"gemini"`.
5.  **Enter API Key:**
    * If using OpenAI: Paste key in `"openai_api_key"`.
    * If using Gemini: Paste key in `"gemini_api_key"`.
    * *(You can get a free Gemini API key at aistudio.google.com)*

## 🎓 How to Study by Priority

Anki doesn't schedule by tags automatically. Use **Filtered Decks** to focus on what matters.

1.  **Run the Prioritizer:** Tools -> AI Prioritization.
2.  **Create a Filtered Deck:** Press `F` in Anki.
3.  **Search String:**
    * Study top priorities only: `deck:"YourDeck" tag:prio:1 is:due`
    * Study everything except junk: `deck:"YourDeck" -tag:prio:4 is:due`
4.  **Build** and study!

## ⚠️ Requirements
* Internet connection.
* Valid API Key (OpenAI or Google Gemini).