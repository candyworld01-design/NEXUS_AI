NEXUS MUSIC 2.0 - DEPLOYMENT NOTES

1) Back up your current GitHub repository before replacing files.
2) Upload these project files to the same repository root.
3) Set environment variables in Render:
   API_ID, API_HASH, BOT_TOKEN, STRING_SESSION, OWNER_ID
   Optional: PORT (Render normally provides this), MAX_QUEUE_SIZE.
4) Keep all secrets in Render Environment, never commit .env or session strings.
5) Deploy and inspect Render logs before testing in a real group.

COMMAND ACCESS
Members:
  /play <song>, /queue, /np, /help

Telegram group admins:
  /playforce <song>, /skip, /pause, /resume, /back, /stop,
  /shuffle, /loop, /volume <1-200>, /remove <position>, /clear

Group owner:
  Same admin controls plus /djmode configuration entry point.

IMPORTANT LIMITATIONS IN THIS FIRST DROP
- Each group has its own in-memory player and queue while the process is running.
- Queues are not persisted across Render restarts.
- The search/download flow still downloads the audio before playback. This favors the
  already-working playback path over a risky unverified direct-stream switch.
- Inline controls are admin-gated.
- DJ allowlists, voting skip, thumbnails/progress bars, favourites, and persistent
  group settings are not implemented in this first drop yet.
- /djmode is currently an owner-only placeholder, not a functioning custom DJ list.
- Back history is in-memory and can disappear after restart.
- Verify PyTgCalls 2.3.3 behavior in your Render environment. In particular test
  multi-group calls, pause/resume, volume, and stream-end events before broad rollout.

TEST PLAN
A. Test one group: /play, /queue, /pause, /resume, /skip, /stop.
B. Test /playforce as an admin while another song is playing; confirm queue remains.
C. Test two groups simultaneously with different tracks.
D. Test a normal member cannot use admin commands or inline controls.
E. Test a Telegram admin can use controls and a member can still /play.
F. Check /health returns healthy after all three components start.

If any test fails, save the exact Render log and the command used. Do not repeatedly
change dependency versions without checking the error first.
