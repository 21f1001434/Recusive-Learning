# Transition Notes: Document Type KB to Rules KB

The Document Type KB phase produced the source/target Document Type IDs and deep profiles. The Rules KB phase uses that as downstream dependency knowledge.

Rule creation/validation generally depends on:

- source Document Type
- target Document Type
- condition operation
- one or more rule conditions
- one or more rule actions
- mapping identifier/version when action is Mapping Transformer
- created/requested by metadata

The Rules KB learner has been added as a separate command so Document Type code remains stable.
