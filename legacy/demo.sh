#!/bin/bash
# demo.sh — Setup clean demo environment and run NL OS Agent

echo "================================================="
echo "       NL OS Agent Demo — Final Year Project     "
echo "  Powered by NVIDIA Nemotron (LM Studio Endpoint)"
echo "================================================="
echo ""

DEMO_DIR="/tmp/agent_demo"
mkdir -p "$DEMO_DIR"
cd "$DEMO_DIR"

# Create sample files for demo
echo "Hello World from NL OS Agent" > hello.txt
echo "print('Demonstrating natural language shell translation with Nemotron')" > script.py
mkdir -p old_logs
echo "2026-10-29: Test error log" > old_logs/system.log

echo "Demo environment prepared at: $DEMO_DIR"
echo "Files created: hello.txt, script.py, old_logs/"
echo ""
echo "Recommended demo walkthrough to rehearse:"
echo "  1. 'show me all files here'               (Auto-executes: ls -la)"
echo "  2. 'find all python files'                (Auto-executes: find . -name '*.py')"
echo "  3. 'create a backup folder'               (State-modifying: mkdir backup)"
echo "  4. 'copy the python file into that folder'(Reference resolution: cp script.py backup/)"
echo "  5. 'delete the old_logs folder'           (Destructive: sandbox simulation + confirmation)"
echo "  6. 'history'                              (Shows SQLite session history)"
echo ""
echo "Launching NL OS Agent..."
echo "-------------------------------------------------"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python "$SCRIPT_DIR/agent.py"
