# Demo script

Rehearsed once from an empty database on 2026-11-07, with Space and Terrestrial 3083, at commit d9fee4e. The TX protection merged after that changes station links but not these steps. It takes about 5 minutes, plus about 2 minutes for the first screening.

It needs Docker, Node and internet access to the ITU. Email goes to a file, so nothing is sent.

## Before you start

Use a fresh Compose project so the database is empty.

```sh
cp .env.example .env
```

In `.env`, set these:

```
IFIC_API_KEY=<output of: openssl rand -hex 32>
IFIC_EMAIL_BACKEND=file
IFIC_MIN_ISSUE=3083
```

`IFIC_MIN_ISSUE=3083` limits the first sync to the newest issue, so the demo doesn't wait for 20 issues.

Start the review UI in its own terminal. If port 5173 is in use, add `-- --port 5174 --strictPort`.

```sh
cd frontend && npm install && npm run dev
```

## 1. A new issue is picked up

```sh
docker compose -p ificdemo up --build -d
docker compose -p ificdemo logs -f sync | grep fetched
```

**Show:** the sync service finds and downloads Space and Terrestrial 3083 by itself. Stop following the log, then wait for screening:

```sh
curl -s 'localhost:8001/issues' | python3 -c "import json,sys; [print(i['domain'],i['issue_no'],i['screen_status']) for i in json.load(sys.stdin)]"
```

Both show `done` after about a minute.

**Open** <http://localhost:5173>. "Latest issue" shows BR IFIC 3083, the comment window and the cases by rule.

**Say:** cron does this on its own schedule. An operator can also press "Check ITU now" or upload a file.

## 2. A case with evidence

Open **Findings**, then a high pfd case. In the rehearsal this was J-BLUEBIRD-NGSO (Japan), `pfd-ngso-app5`, at `/findings/142`. IDs can change between runs.

**Show:**

- The title: it exceeds the trigger in a one-day flight, under worst-case assumptions.
- The calculation: power, gain, and the value of -86.3 dB against -123.0 dB, a margin of +36.7 dB.
- The worst point, with its latitude, longitude, angle and minute of the flight.
- The evidence panel: source file hash, notice ID and regulation citation.
- The assumptions.

**Say:** the numbers come from deterministic rules. No AI decided this case. Maximum gain is applied towards Finland because the beam contours aren't available, so this is a possible concern for an expert.

For contrast, show a case titled "upper bound above the limit". It isn't presented as an exceedance.

## 3. Expert review

In "Your review", enter a note and press **Accept**. The badge changes to Accepted.

**Say:** accepting sends nothing. If the filing changes in a later issue, the case returns to review. The browser never holds the API key, because the dev server's proxy adds it.

## 4. Draft letter

The API draft is the one with the guardrails. Use the case ID from step 2:

```sh
curl -s localhost:8002/cases/142/draft | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['status'], 'sent:', d['sent']); print(d['letter'])"
```

**Show:** the banner "DRAFT FOR EXPERT REVIEW. NOT SENT.", the source data block, and the placeholders the expert completes.

**Say:** this template needs no language model. With `ANTHROPIC_API_KEY` set, `?use_llm=true` asks a model to rewrite it. A guardrail checks each number, date and citation against the case and rejects claims the evidence doesn't support, then falls back to this template.

The language-model path was tested with a fake model only. The rehearsal didn't call a real one.

## 5. Digest email

```sh
docker compose -p ificdemo exec notifier sh -c 'ls /app/data/outbox; grep -h ^Subject /app/data/outbox/*.eml'
```

**Show:** in the rehearsal, the subjects were:

```
BR IFIC 3083 (space): 121 possible concerns for review, 36 high
BR IFIC 3083 (terrestrial): 80 possible concerns for review, 0 high
```

**Say:** there's one digest per issue, not one email per case. It lists severity, rule, notice IDs, the estimated deadline and a review link. Screening the same issue again adds no cases and sends no email.

## If you have more time

- Show `outputs/ific_3074/` and `outputs/ific_3076/`, the evaluation files.
- Show `outputs/hand-check.md`: what was independently rechecked, and what can't be measured.
- Show the "Known limitations" section of the README.

## Clean up

This deletes the demo project's containers and its volumes only.

```sh
docker compose -p ificdemo down -v
```

## What the rehearsal did not cover

The browser was driven with headless screenshots, and the review was saved through the UI's proxy API rather than by a mouse click on Accept. The draft letter step, the digest and the screening-again check were run on the same stack. Press Accept once yourself before presenting.
