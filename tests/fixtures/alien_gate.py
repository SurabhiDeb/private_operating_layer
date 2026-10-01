"""A gate in a style neither reference product uses: a different language idiom, a
different way of selecting runs, and a bar written as a percentage."""

MIN_CORRECT_PCT = 92

def check(every_run):
    for run in every_run:
        correct = len([c for c in run["checks"] if c["suggested"] == c["wanted"]])
        if correct * 100 / len(run["checks"]) < MIN_CORRECT_PCT:
            raise SystemExit("below the bar")
