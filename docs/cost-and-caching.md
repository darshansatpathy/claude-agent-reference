# Cost and prompt caching

## Prices are configuration

`prices.example.toml` holds **placeholder** numbers. Copy it, put in the rates from your own
pricing page or agreement, and load it with `PriceTable.load(path)`. The library never ships a
rate as if it were fact, because rates change and differ by contract. If you set a cost budget
and the model has no price, the agent raises `ConfigError` before sending anything.

Cost is estimated from the `usage` block of each response:

```text
cost = input × price_in
     + output × price_out
     + cache_write × price_in × cache_write_multiplier
     + cache_read  × price_in × cache_read_multiplier
```

The multipliers depend on the cache lifetime you choose, so they are in the file too. Verify
them against current pricing. Batch work can carry a discount (`batch_discount`).

## Caching

Agents resend the same long prefix on every turn: instructions, tool definitions, earlier
turns. Prompt caching lets the API reuse that prefix at a lower price and latency.

`with_cache_control` marks three breakpoints on a **copy** of each request:

1. the end of the system prompt,
2. the last tool definition,
3. the last block of the conversation so far.

Because the markers are added at request time, the stored history never accumulates them (the
API accepts only a few per request). Each turn the third marker moves forward, so the growing
conversation is cached incrementally.

What to watch:

- **Minimum length.** Prefixes shorter than the model's minimum cacheable size are processed
  normally and simply not cached. Adding a marker is always safe.
- **Lifetime.** The default is short. `cache_ttl="1h"` asks for a longer one, priced
  differently.
- **Anything that changes the prefix breaks the cache.** Timestamps in the system prompt, tool
  lists whose order changes, and edited earlier turns all do. Tool definitions are sorted for
  this reason.
- **Verify it works.** `RunResult.usage.cache_read_input_tokens` should be large from the
  second turn on. If it stays at zero, the prefix is too short or is changing.

## Budgets

`Budget(max_cost_usd=...)` stops the run before the next model call once estimated spend
reaches the limit. The check is *before* a call, so a single large call can overshoot by up to
its own cost; set the limit with that in mind, together with `max_total_tokens`.
