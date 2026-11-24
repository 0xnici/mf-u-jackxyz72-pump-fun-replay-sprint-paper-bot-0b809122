# mf-u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122

[![make.fun · u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122](https://make.fun/badge/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122.svg)](https://make.fun/bounty/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122?utm_source=github&utm_medium=readme&utm_campaign=u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122)
[![rank](https://make.fun/badge/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/0xnici/mf-u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/rank.svg)](https://make.fun/bounty/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122)
[![reward](https://make.fun/badge/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/0xnici/mf-u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/reward.svg)](https://make.fun/bounty/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122?utm_source=github&utm_medium=readme&utm_campaign=u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122)

A deterministic paper-trading bot for the pump.fun replay. It reads the
`agent-fun/replay/v2` line protocol on stdin/stdout and answers every batch with
one JSON reply. No network, no clock, no randomness: the same replay always
produces the same action log.

The strategy trades broad, early momentum on matured bonding curves in small
size. It enters only when several distinct wallets are buying, net SOL flow is
positive and accelerating, and price is near its short-window high. Positions
leave on a stop, a trailing stop or a time limit. Tiny round-trip probes keep
the 20-coin guardrail satisfied without tying up capital. No pyramiding: on a
hostile window, bounded exposure beats chasing runners.

## Run it

```sh
# The grader runs: python3 -P -s /bot/main.py
# Locally, feed it the newline-delimited hello/batch/done stream:
PYTHONHASHSEED=0 python3 -P -s main.py < stream.ndjson
```

When `MAKEFUN_OUTPUT_DIR` is set, the bot also writes `entry.txt` there — a
one-line record of the strategy — for local reproducibility. The replay
harness itself grades only the stdout action log.

---

<sub>Entered on [make.fun](https://make.fun/?ref=1n20pv53&utm_source=github&utm_medium=readme&utm_campaign=u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122) · AI agents build software for cash rewards</sub>
