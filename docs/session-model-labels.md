# Session model labels

The Sessions model column and detail drawer follow the selected date range,
model/provider filters, and Copilot precision setting. A session that used Sonnet
last week and Fable today shows Fable when Today is selected.

When more than one model has recorded usage in that scope, the leading label is
the model with the most total input, output, cache-read and cache-write tokens.
Recorded calls and replies break token ties, followed by the model name. The
`+N` count includes every matching model. Names are bounded to six in the tooltip
and drawer; additional models are stated explicitly.
Model sorting follows the leading scoped label. Session search can match every
recorded model in the selected scope, including names beyond the six displayed.

A user-only, compaction-only or all-zero scope shows **No model usage**. Recorded
calls or replies can still identify a model when token counts are zero. Older
entries without model-by-day detail retain their historical model names with a
visible **Whole session** label and an explanation in the drawer. This limitation
does not mean that the old model ran in the selected range.

This is display metadata derived from existing maps. Stored historical model
names, complete date/model maps, accounting vectors, source files and the durable
ledger are unchanged. No rebuild or cache-version migration is needed.
