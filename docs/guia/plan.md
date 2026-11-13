# Plan del agente

Como en Codex y Claude Code, el agente lleva una lista de tareas con `update_plan`
(pasos `pending`, `in_progress` o `completed`, y una `explanation` opcional). La
app la enseña bajo la conversación mientras trabaja o le quedan pasos. Si
escribes mientras tanto, se queda como «Plan de antes de tu último mensaje» hasta
que el agente mande otro. `--stdio`: evento `plan`
(`{"pasos":[{"paso":"…","estado":"in_progress"}],"explicacion":"…"}`), también al
reanudar una sesión.
