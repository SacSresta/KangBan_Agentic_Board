Frontend adaptations retain the complete MIT notices in this directory.

- `frontend/components/board.tsx`: dnd-kit sensor, sortable task, typed drag-data, overlay, and cross-column interaction patterns adapted from George Griffiths' `src/components/KanbanBoard.tsx`, `BoardColumn.tsx`, and `TaskCard.tsx`. Source: https://github.com/Georgegriff/react-dnd-kit-tailwind-shadcn-ui (retrieved 2026-10-06). Our adapter persists moves through FastAPI and gates human-controlled states.
- `frontend/components/activity.tsx`: JSON recognition, tabular tool values, preview truncation, and expandable results adapted from LangChain Agent Chat UI `src/components/thread/messages/tool-calls.tsx`, revision `cf72cb0f68a04d24db93eb19afb2d46f3a5261d4`. Source: https://github.com/langchain-ai/agent-chat-ui (retrieved 2026-10-06). Our adapter uses local ordered Activity events and React Markdown, without the LangGraph Server SDK or protocol.

Only component patterns are ported; dependency manifests and build configuration belong to this project.
