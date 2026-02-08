#!/bin/bash

# 1. Branch erstellen (oder wechseln, falls er existiert)
BRANCH_NAME="fix/tests-and-ci"
echo "Erstelle/Wechsle zu Branch: $BRANCH_NAME"
git checkout -b $BRANCH_NAME 2>/dev/null || git checkout $BRANCH_NAME

# 2. Dateien hinzufügen
echo "Füge geänderte Dateien hinzu..."
git add tests/test_leave_home_handler.py
git add .github/workflows/run_tests.yml

# Optional: Falls du requirements angepasst hast
if [ -f requirements-dev.txt ]; then
    git add requirements-dev.txt
fi

# 3. Commit erstellen
echo "Erstelle Commit..."
git commit -m "fix(tests): switch to unittest and add CI workflow

- Refactor test_leave_home_handler.py to use unittest instead of pytest to avoid dependency issues.
- Add .github/workflows/run_tests.yml to run tests on push/PR.
- Mock external dependencies (const, genai_client) in tests to ensure isolation."

echo "Fertig! Du kannst den Branch nun pushen:"
echo "git push origin $BRANCH_NAME"
