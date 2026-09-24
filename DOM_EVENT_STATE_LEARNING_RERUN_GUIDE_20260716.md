# DOM Event Learning Rerun Guide

Run the same no-save full command. No new CLI option is required because DOM event and mutation capture are enabled by default.

Relevant `config.yaml` settings:

```yaml
extraction:
  collect_dom_clicks: true
  collect_dom_events: true
  collect_dom_mutations: true
  max_dom_event_records: 6000
  max_dom_mutation_records: 6000
```

During the next run, inspect these files for every parent selection:

```text
dom_events/action_dom_transitions.json
dom_events/dom_event_summary.json
<phase>/stateful_*_execution.json
<phase>/portal_form_knowledge/*
```

A valid parent-child learning record should show:

1. parent `input/change/focusout` events;
2. added or enabled child control;
3. semantic section and row context;
4. exact child input path;
5. exact committed child value;
6. section-judge approval before promotion.

The agent must not treat a click or mutation alone as success. The final committed portal value remains the acceptance condition.
