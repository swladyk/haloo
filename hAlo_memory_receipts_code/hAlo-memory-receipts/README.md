# Memory, with receipts — team hAlo

Our entry for the Reactive AI **Memory Retrieval Challenge** (START Warsaw × Poland Tech Week Hackathon, 30–31 Oct 2026).

Our code finds the few parts of a long, noisy chat history that answer a question, sends them to Reactor with their dates, and Reactor gives the final answer with citations, or `INSUFFICIENT_EVIDENCE`.

## How our search works

1. **Search every message.** BM25 over every turn, user *and* assistant, so "what did you recommend?" questions work too.
2. **Rank conversations by their best turns** (best turn + 0.3 × the next two).
3. **Keep nearby personal lines.** For the two best conversations we also keep the user turns around the match: the fact often comes one message after it.
4. **Trim long answers** to the paragraphs and list items that match the question, so more conversations fit in the 12,000-character budget.
5. **Oldest to newest, with dates.** Excerpts are sent in date order, each labelled with its session date, so Reactor sees what changed and the latest value wins.
6. **Fix 1 — every answer cites a chat.** When Reactor answers without naming a chat, `add_citations.py` re-runs our search and cites the sent chat that contains the answer (on dev: 92% of these added citations are correct).
7. **Fix 2 — a wider second pass.** Questions where Reactor said `INSUFFICIENT_EVIDENCE` are asked again with up to 14 conversations instead of 8, filling the message to its 16,000-character limit (`reask_wide.py`). On dev this lifts all-gold-chats-reach-Reactor from 89% to 94%. On test it turned 11 of 35 abstentions into real answers (35 → 24 abstained, close to the ~8% truly unanswerable).
8. **Robust runs.** A dropped chat (`closed`) or a replaced one (`history_changed`) starts a fresh Reactor history and asks again; finished answers are kept and only unanswered questions are re-asked.

## Results on the practice set (`dev`, official gold)

| Search | All gold chats reach Reactor |
|---|---|
| Kit's starter search | 61% |
| Our search | **89%** |
| Our wider second pass | **94%** |

(494 answerable questions.) Live with Reactor on the first five practice questions: 1 of 5 correct with the starter search, up to 4 of 5 with ours.

## How to run

Needs Python 3.11+ and `websockets>=14`. The organisers' kit (`client.py`, `predictions.py`) is included. Download the challenge data (`dev.tar.gz`, `test.tar.gz`) from the [RxAI-dev/memory-challenge Releases page](https://github.com/RxAI-dev/memory-challenge/releases) and unpack it here, so `dev/` and `test/` sit next to these files. The access key is typed at a hidden prompt and never stored in this repo.

```sh
python3 -m venv .venv && . .venv/bin/activate && pip install "websockets>=14"
printf 'Access key: '; read -rs REACTOR_ACCESS_KEY; echo; export REACTOR_ACCESS_KEY

python final_run2.py --questions test/questions.jsonl --histories test/histories.jsonl \
    --base-url https://tNN.api.reactor-hackathon.rxai.dev --agent-id agt_... --out test_results.jsonl
python add_citations.py test_results.jsonl --questions test/questions.jsonl --histories test/histories.jsonl \
    --out test_results_cited.jsonl
python reask_wide.py test_results_cited.jsonl --questions test/questions.jsonl --histories test/histories.jsonl \
    --base-url https://tNN.api.reactor-hackathon.rxai.dev --agent-id agt_... --out test_results_v2.jsonl
# (if stopped early: python keep_reask.py test_results_cited.jsonl test_results_v2.jsonl.reask --out test_results_v2.jsonl)
python add_citations.py test_results_v2.jsonl --questions test/questions.jsonl --histories test/histories.jsonl \
    --out test_results_v2_cited.jsonl
python predictions.py test_results_v2_cited.jsonl --histories test/histories.jsonl \
    --out predictions.jsonl --abstain-on agent,no-answer
```

`predictions.jsonl` is the output of exactly these commands on the test split: 146 predictions, 24 abstained. (The second pass was stopped at the deadline after 20 of 35 questions; `keep_reask.py` kept its answers.)

## Live demo

```sh
python live_demo7.py --base-url https://tNN.api.reactor-hackathon.rxai.dev --agent-id agt_...
```
then open http://127.0.0.1:8800 (`memory_receipts_live6.html` must sit next to it). Pick a person, ask anything, Reactor answers live, and "Would you like to know where I got this?" prints a memory receipt with the exact chat.

## Files

- `final_run2.py` — our runner: the smart search plus the Reactor loop with retries
- `add_citations.py` — Fix 1: cites a chat for answers Reactor left uncited
- `reask_wide.py` — Fix 2: re-asks abstentions with a wider search
- `keep_reask.py` — keeps the second pass's answers if it is stopped early
- `live_demo7.py`, `memory_receipts_live6.html` — the live demo
- `client.py`, `predictions.py` — the organisers' kit (we changed `splitlines()` to `split("\n")` in `predictions.py`, because some chats contain Unicode line separators that broke parsing)
- `predictions.jsonl` — our submission

Mariam Marciniak · team hAlo
