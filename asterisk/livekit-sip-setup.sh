
lk sip create-inbound-trunk \
  --name "dev-trunk" \
  --numbers "+1234567890"

lk sip create-dispatch-rule \
  --trunk-ids "<TRUNK_ID_FROM_STEP_1>" \
  --new-room \
  --room-prefix "sip-"

# ─── Step 4: Verify Configuration ───────────────────────────────────────────

# List trunks:
lk sip list-trunk

# List dispatch rules:
lk sip list-dispatch-rule

# List active SIP participants:
lk sip list-participant
