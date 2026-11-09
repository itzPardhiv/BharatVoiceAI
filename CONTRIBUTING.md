# Contributing to BharatVoice AI

Thank you for your interest in contributing to **BharatVoice AI**! We welcome contributions to improve multilingual retail workflows, voice assistant capabilities, and financial safety mechanisms.

---

## 🛠️ Development Setup

### 1. Prerequisites
- **Python 3.10+** (Python 3.11 recommended)
- **Node.js 18+** and **npm 9+**
- **Git**

### 2. Clone and Setup Environment
```bash
# Clone the repository
git clone https://github.com/itzPardhiv/BharatVoiceAI.git
cd BharatVoiceAI

# Setup backend
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env

# Setup frontend
cd frontend
npm install
cp .env.example .env
cd ..
```

### 3. Running Locally
- **Backend:** `python -m uvicorn app.main:app --app-dir backend --reload --port 8000`
- **Frontend:** `npm run dev --prefix frontend`
- **Application URL:** `http://localhost:5173/`
- **API Documentation:** `http://localhost:8000/docs`

---

## 🧪 Running Tests & Quality Checks

Always verify that all tests pass before opening a pull request:

```bash
# Run backend pytest suite (119 unit & integration tests)
pytest

# Verify frontend TypeScript types and build
npm run build --prefix frontend
```

---

## 📐 Guidelines

1. **Keep Secrets Private:** Never commit `.env` files, API keys, or private database files.
2. **Double-Entry Accounting Integrity:** Any financial ledger modification must preserve debit/credit parity.
3. **Multilingual Support:** Ensure localized strings in `frontend/src/i18n.tsx` are maintained when adding new UI components.
4. **Clean Commits:** Write concise, imperative commit messages (e.g., `feat: add Tamil voice intent mapping`).

---

## 📄 License
By contributing to BharatVoice AI, you agree that your contributions will be licensed under the project's [MIT License](LICENSE).

