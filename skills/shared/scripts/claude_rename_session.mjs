import { renameSession } from '@anthropic-ai/claude-agent-sdk';

const [sessionId, title, dir] = process.argv.slice(2);
if (!sessionId || !title || !dir) {
  throw new Error('usage: claude_rename_session.mjs <session-id> <title> <project-dir>');
}
await renameSession(sessionId, title, { dir });
