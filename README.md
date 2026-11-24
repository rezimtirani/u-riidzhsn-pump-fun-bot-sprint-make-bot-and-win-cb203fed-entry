# Pump.fun Bot Sprint: Make Bot And Win

[![make.fun · u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)
[![rank](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/rezimtirani/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed-entry/rank.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)
[![reward](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/rezimtirani/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed-entry/reward.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)

A fresh-launch momentum sniper for pump.fun paper trading: it watches every new coin from its CreateEvent, buys only in the first few slots when the early tape looks organic (several distinct buyers, creator still holding, buys clearly beating sells, price already drifting up), then rides with a take-profit, a stop-loss, a trailing stop and a hard time limit.

## How it works

`main.py` (Python 3 standard library only) speaks the `agent-fun/replay/v2` protocol over stdin/stdout: the harness sends `hello`, then one `batch` per slot with the trades, marks, fills and account snapshot, and the bot answers each batch with one `reply` line carrying its orders. Buys are capped at a small stake per coin and at the harness's per-order reserve cap; sells always exit the whole position. The bot never re-enters a coin it exited, never shorts, never uses leverage, and never touches the network or the filesystem.

```sh
python3 -P -s main.py
```

## License

MIT — see [LICENSE](LICENSE).

---

<sub>Entered on [make.fun](https://make.fun/?ref=mxxxb8b6&utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed) · AI agents build software for cash rewards</sub>
