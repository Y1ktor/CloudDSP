# AGENTS.md

## 1. Overview & Architecture

**Project Goal:** CloudDSP is a cloud-powered, web-based digital audio workstation (DAW). It provides advanced audio capabilities like stem splitting, MIDI extraction, piano roll editing, and DSP effects (EQ, flangers, etc.) by combining a heavy React client with scalable AWS cloud infrastructure.

**Tech Stack:**
- **Frontend:** React 19, Vite, `@tonejs/midi`, `smplr`.
- **Backend (DSP/Cloud):** Python (3.11+), Boto3, `pedalboard`, `demucs`, `yt-dlp`. 
- **Infrastructure:** AWS (Lambda, Batch, S3, API Gateway WebSockets), AWS CloudFormation (IaC), Docker.

**Key Directories:**
- `/frontend-react/`: The Vite + React web application.
  - `src/components/`: UI components (e.g., `StemSplitter`, `MidiEditorPopup`).
  - `src/hooks/`: Crucial separation of concerns. Audio scheduling, undo history, and drag logic are isolated in custom hooks (e.g., `useMidiSynth.js`, `useMidiEditorOperations.js`).
- `/src/DSP/`: Python backend scripts for digital signal processing.
  - `src/Cloud/`: AWS deployment targets (Lambda handlers, WebSocket notification scripts).
  - `docker/`: Dockerfiles for deploying heavy ML tasks (`stem_split`, `basic_pitch`, `yt-dlp`) to AWS Batch/Lambda.
  - `tests/`: Manual verification scripts for Python DSP logic.
- `/IaC/`: Infrastructure as Code (CloudFormation YAML templates).

## 2. Setup & Environment

**Prerequisites:** Node.js, Python 3.11/3.14, Docker, AWS CLI.

**Frontend:**
```bash
cd frontend-react
npm install
npm run dev      # Start Vite local development server
npm run build    # Build for production
```

**Backend (Python/DSP):**
Activate one of the existing Python virtual environments (e.g., `src/DSP/venv3.11`) and install the respective dependencies from the `docker/` requirements files if testing locally.


## 3. Critical Rules & Guardrails ("Do's and Don'ts")

- **NEVER Commit Secrets:** Do not hardcode AWS credentials, proxy IPs, or API keys in the source code. Always extract these to environment variables (`os.environ.get(...)`) and pass them dynamically via the IaC templates or local `.env` files.
- **Respect React Render Cycles:** Do *not* tie high-frequency audio logic or playhead rendering directly to React state (`useState`), as this causes severe UI jitter and frame drops. Use `useRef` and `useLayoutEffect` to mutate the DOM or audio context directly.
- **Do Not ClickOps AWS:** If infrastructure needs changing, do not modify AWS resources manually. Update the YAML templates in the `/IaC/` directory.
- **Immutable MIDI Data:** When interacting with `@tonejs/midi`, deep clone instances rather than mutating globally shared state, to avoid irreversibly corrupting the undo history.

## 4. Git & Workflow Preferences

- **Commit Messages:** Follow standard Conventional Commits format. Look at the `.gitmsg` template in the root directory for guidance. 
  - Valid prefixes: `feat:`, `fix:`, `refactor:`, `chore:`.
- Ensure changes are atomic. Do not mix unrelated refactors with feature additions in a single commit.
