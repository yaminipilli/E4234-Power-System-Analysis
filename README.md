# E4234 — Newton–Raphson Power-Flow Analysis and GUI

This repository contains a Python implementation of an AC power-flow engine
using the Newton–Raphson method, MATPOWER case-file import, benchmark scripts,
a Tkinter GUI, and automated tests.

> **Validation note:** `validate.py` checks case import and convergence using
> this project's solver. It is not an independent comparison against MATPOWER.
> In particular, slack-generator reactive-power-limit enforcement and
> reference-bus reassignment must be verified against an independent tool
> before claiming complete Q-limit compliance.

## 1. Requirements

- Python 3.10 or later
- NumPy
- SciPy
- Matplotlib (GUI plotting features)
- openpyxl (Excel export features, if used)
- reportlab (PDF report features, if used)
- Tkinter (normally included with standard Windows Python installations)

## 2. Installation

Open a terminal in the project root (the folder containing this README) and run:

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Windows Command Prompt:

```bat
.venv\Scripts\activate.bat
```

Linux/macOS:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 3. Launch the GUI

From the project root:

```bash
python gui_app.py
```

On Windows, you can alternatively double-click `start_gui.bat`.

## 4. Run the test suite

```bash
python -m unittest discover -s tests -v
```

Compile-check the main modules:

```bash
python -m py_compile network.py solver.py matpower_import.py gui_app.py validate.py
```

## 5. Run validation on bundled IEEE cases

Run all bundled cases (IEEE 14, 30, 57 and 118):

```bash
python validate.py
```

Run only IEEE 14:

```bash
python validate.py --cases 14
```

Run with the solver's implemented PV-bus reactive-power-limit logic enabled:

```bash
python validate.py --q-limits
```

The script prints a summary and creates:

- `validation_results.json` — detailed bus, generator, switching and summary data.
- `validation_results.csv` — compact case-level summary.

These outputs are generated locally and are ignored by Git. The `--q-limits`
option exercises the current implementation; it must not be interpreted as
proof that slack-generator Q limits and reference-bus reassignment are fully
implemented. Compare against MATPOWER with matching data and options before
making that claim.

## 6. Run the benchmark

```bash
python benchmark.py
```

The benchmark runs each bundled case repeatedly and reports median execution
time, iteration count, maximum mismatch and active-power loss. The current
benchmark intentionally runs with reactive-power limits disabled so that the
base Newton–Raphson solutions can be compared consistently.

## 7. Run the IEEE 14-bus example

```bash
python run_ieee14.py
```

## 8. Project layout

```text
E4234/
├── benchmark.py
├── gui_app.py
├── matpower_import.py
├── network.py
├── solver.py
├── validate.py
├── run_ieee14.py
├── requirements.txt
├── start_gui.bat
├── data/
│   ├── case14.m
│   ├── case30.m
│   ├── case57.m
│   └── case118.m
└── tests/
    ├── test_ieee14.py
    ├── test_jacobian.py
    ├── test_q_limits.py
    ├── test_shunt_out_of_service.py
    ├── test_solver.py
    └── test_transformer.py
```

## 9. Git workflow and submission

Create a private GitHub or GitLab repository and push the project using your
own account. Commit meaningful changes as they are made; do not manufacture or
backdate commit timestamps. If the work is being organized now, use truthful,
module-focused commits such as:

```bash
git init
git add network.py matpower_import.py
git commit -m "Add network model and MATPOWER case importer"

git add solver.py tests/
git commit -m "Add Newton-Raphson solver and unit tests"

git add gui_app.py start_gui.bat
git commit -m "Add desktop GUI"

git add benchmark.py run_ieee14.py validate.py
git commit -m "Add benchmark and validation utilities"

git add README.md requirements.txt .gitignore data/
git commit -m "Document setup and include IEEE test cases"
```

Then connect your remote repository and push:

```bash
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
git push -u origin main
```

Replace the URL with your actual private repository URL. Share that link with
the instructor and TAs as required by the course.

## 10. Submission ZIP

Submit one ZIP named `E4234_Name.zip` (replace `Name` with your name), containing
the project source, tests, case data, README and requirements file. Exclude
`.git`, virtual environments, `__pycache__`, and generated validation outputs.

## 11. Known limitations to disclose

- Independent MATPOWER comparison has not yet been completed.
- Slack-generator Q-limit enforcement and reference-bus reassignment require
  further implementation and regression testing.
- Transformer-specific nameplate/%Z conversion, full ZIP-load support,
  out-of-service island handling, and some advanced GUI workflows may require
  further verification depending on the assignment rubric.
- Custom branches created in the GUI may contain placeholder impedances;
  replace them with validated network data before engineering use.
