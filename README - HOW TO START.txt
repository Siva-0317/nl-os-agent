========================================================================
   NL-OS-Agent: Safety-Verified AI-Native Operating System Assistant
                       QUICK START GUIDE
========================================================================

Thank you for trying out NL-OS-Agent!
This application runs 100% locally and privately on your Windows PC.
No cloud connection, no API keys, and zero telemetry required.

------------------------------------------------------------------------
STEP 1: LAUNCH THE APPLICATION
------------------------------------------------------------------------
Double-click:
    nlagent-gui.exe
    (or double-click "Start NL-OS-Agent.bat")

------------------------------------------------------------------------
STEP 2: FIRST-TIME SETUP (One-Time Model Download)
------------------------------------------------------------------------
Because this assistant runs real AI completely offline on your computer,
it requires an AI model file:
1. On the first launch, the "Setup Wizard" will automatically appear.
2. Click "Next" past the Intro and Hardware Detection screens.
   (The agent automatically configures hardware: NVIDIA CUDA GPUs, 
    Intel/AMD CPUs, and multi-core CPU threads are all auto-tuned!)
3. On the "Model & Runtime Setup" page, click:
        [ Download / Resume Selected ]
   This will download the official fine-tuned model:
   "NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf" (~2.8 GB) directly
   from Hugging Face into your models/ folder.
4. Once the download finishes, click "Next", then "Finish".

------------------------------------------------------------------------
STEP 3: START USING NL-OS-AGENT!
------------------------------------------------------------------------
The Chat Assistant window will appear with a green badge:
    [ READY - Model Loaded ]

Type any natural language request into the prompt box at the bottom, e.g.:
  • "show all hidden files in C:\"
  • "find all python files modified today"
  • "check free disk space"
  • "create a folder named 'Project_Backup' on my desktop"
  • "delete temp log files"

Click [Propose] (or press Enter).
The agent will:
  1. Translate your plain-English prompt into safe, accurate PowerShell.
  2. Parse the command using deterministic Abstract Syntax Trees (AST).
  3. Classify safety: READ ONLY (green) or DESTRUCTIVE (red warning).
  4. Automatically back up files to Quarantine before any destructive run!

------------------------------------------------------------------------
QUARANTINE & SAFETY UNDO
------------------------------------------------------------------------
If any command modifies or removes files, you can reverse it anytime:
    Click [ Undo Last ] in the top-right corner of the chat window
    (or run "nlctl undo" in terminal)
Your files are instantly restored byte-for-byte from the local quarantine.

------------------------------------------------------------------------
FREQUENTLY ASKED QUESTIONS (FAQ)
------------------------------------------------------------------------
Q: Do I need an NVIDIA GPU?
A: No! The application automatically checks for an NVIDIA GPU with CUDA.
   If none is found, it automatically falls back to CPU mode with AVX2
   acceleration and multi-threading.

Q: Windows SmartScreen shows "Windows protected your PC"?
A: Click "More info" -> "Run anyway". This is an academic research 
   open-source build and is not signed with an enterprise certificate.

Q: Can I use the CLI / Terminal?
A: Yes! Open PowerShell inside this folder and run:
       .\nlctl.exe doctor
       .\nlctl.exe propose "list all files in my documents"
========================================================================
