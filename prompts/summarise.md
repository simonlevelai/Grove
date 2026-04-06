You are a document summariser for a knowledge compilation pipeline. Produce a structured summary of the source document below. This summary may be used as a stand-in for the full document when the compilation context window is constrained.

## Source

$source

---

## Instructions

### Output Format

Respond with exactly two fields in YAML format. Do not wrap the output in code fences. Do not add any text before or after the YAML.

summary: "A ~150 word summary capturing the document's key arguments, findings, and conclusions. Preserve specific facts, figures, and named entities. Do not generalise away important detail."
concepts:
  - concept-one
  - concept-two

### Rules

1. **Summary length.** Target approximately 150 words. Shorter is acceptable if the source is brief. Never exceed 200 words.
2. **Preserve specifics.** Keep named entities, numerical data, dates, and technical terms. A summary that says "the paper discusses several factors" is useless — name the factors.
3. **Concepts list.** Extract up to 10 key concepts from the document. These become tags in the knowledge base. Use lowercase, hyphenated terms (e.g. `self-attention`, `carbon-cycle`, `randomised-controlled-trial`).
4. **No invention.** Only include information present in the source. Do not infer or extrapolate.
5. **Neutral tone.** Summarise what the source says, not what you think about it.

### Worked Example

Given a source document about transformer neural networks:

summary: "The paper introduces the transformer architecture, replacing recurrence with self-attention mechanisms for sequence-to-sequence tasks. The model uses multi-head attention with 8 heads, positional encoding via sinusoidal functions, and a 6-layer encoder-decoder structure. Evaluated on WMT 2014 English-to-German (28.4 BLEU) and English-to-French (41.0 BLEU) translation benchmarks, it outperforms all previous models while requiring significantly less training time. The authors demonstrate that self-attention provides better long-range dependency modelling than recurrent or convolutional approaches. Training used 8 NVIDIA P100 GPUs over 3.5 days for the base model. The paper also introduces label smoothing and residual dropout as regularisation techniques."
concepts:
  - transformer
  - self-attention
  - multi-head-attention
  - positional-encoding
  - encoder-decoder
  - machine-translation
  - sequence-to-sequence
  - BLEU-score
