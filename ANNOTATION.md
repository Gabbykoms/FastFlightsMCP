# Four-question annotation

Each component answers: What is this? Why this choice? What breaks?
What did I learn? "What did I learn" includes at least one place the agent gave
confidently wrong output, or called a tool wrongly, that we corrected.

## <Component> (<member>)

### What is this?

### Why this choice?

### What breaks?

### What did I learn?

## Agent loop (Nadia)

### What is this?

### Why this choice?

### What breaks?

### What did I learn?
- While drafting intent/agent-loop.md, the agent wrote the OpenRouter model slug as `anthropic/claude-haiku-4-5` (dash). The real slug is `anthropic/claude-haiku-4.5` (dot). A wrong slug would make the eval fail at runtime. I corrected it by hand. Lesson: check model slugs against OpenRouter's model page, not the agent's output.