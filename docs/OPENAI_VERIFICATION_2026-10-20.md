# GPT-6 Luna integration verification — 20 October 2026

The supplied OpenAI key successfully authenticated and generated a live response from `gpt-6-luna`. Active `.env` configuration and application defaults now select GPT-6 Luna with `low` reasoning and a 4,096-token output ceiling. The token ceiling covers reasoning plus visible output; it is a maximum, not a fixed charge. The key was preserved without printing it and remains ignored by Git.

The Responses API and strict proposal schema remain in use. No temperature or sampling parameters were added. Model, reasoning effort, and output ceiling are recorded with each new experiment and reused during execution. [Official GPT-6 Luna documentation](https://developers.openai.com/api/docs/models/gpt-6-luna) confirms support for low reasoning and structured outputs.

## Executed checks

| Check | Result |
| --- | --- |
| Live authentication/generation | Passed; requested and returned model both `gpt-6-luna` |
| Python regression suite | 128 passed, no failures or skips |
| Normal browser suite | 8 passed, including the actual FastAPI worker and LLVM |
| Live OpenAI browser workflow | Passed: full experiments for dot, matmul, histogram; evidence inspection; downloads; charts; cancellation; retry; saved-run reload; no uncaught browser errors or failed HTTP responses |
| Live plain/graph compiler matrix | All six combinations passed, four candidates each with tuning and held-out confirmation |
| Live standalone CLI | Passed |
| Live API worker | Passed; automatic OpenAI configuration, completed proposal, and artifact downloads |
| Clean install | Passed: sdist, wheel, fresh venv, installed CLI experiment, report export, portable example |
| Dependency consistency and JS syntax | Passed |

The final standalone live checker recorded 10 successful stages. Its six compiler experiments attempted 12 guided proposals: 10 model proposals and two explicitly labeled local fallbacks. Eleven guided candidates passed compiler/correctness/timing validation; one was correctly rejected because it made no IR change. Fallbacks remain visible as `llm_fallback`; they are not counted as model proposals. Random comparator candidates never call OpenAI.

These checks establish functionality on small test workloads. They do not establish a compiler speedup or prove that low reasoning outperforms other reasoning levels. No higher reasoning setting was needed to complete verification.

## Reproduction

From the checkout, using the configured Ubuntu WSL virtual environment:

```bash
python -m autotuner llm-status
python -m pytest -q
python scripts/check_openai.py --workflows
python scripts/check_package.py
```

Normal browser regressions:

```bash
npm test
npm run test:live
```

Explicit live OpenAI browser check (uses API credits):

```bash
AUTOTUNER_LIVE_OPENAI=1 npm run test:live
```

Browser checks were executed on Windows using the bundled Playwright module and the installed Chromium executable via `PLAYWRIGHT_MODULE_PATH` and `BROWSER_EXECUTABLE`. Browser requests were bridged to the real ASGI application over stdio, so verification did not require taking over a listening port. Browser and standalone live checks used disposable experiment stores, preserving existing saved runs.

Local evidence: `test-results/openai-live.json`, `test-results/browser-openai-live.png`, and `test-results/browser-gpt6-luna.png`. These generated files are ignored by Git. Normal tests stay offline; live requests require explicit opt-in. Restart an already running server to load the new `.env` configuration.
