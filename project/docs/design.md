---
id: BASIC-RESULT
kind: basic
depends_on: [REQ-RESULT]
---

# Design

A worker changes the product file. An independent verifier evaluates two assertions.
The controller records the result and never treats the worker's own completion message as verification.
