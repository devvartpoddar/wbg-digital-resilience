"""The tests' fake decision models answer one item a request; the batched
form (src/answers.py BATCH) has tests of its own (tests/test_answers_batch.py)."""
import os

os.environ.setdefault("WBG_ASK_BATCH", "0")
