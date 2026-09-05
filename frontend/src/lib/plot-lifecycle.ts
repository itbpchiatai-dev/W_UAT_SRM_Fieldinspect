/**
 * "One plot, one cycle" — the round-E policy, in one place (round E).
 *
 * A plot is registered for a season, inspected through it, and finalized. It is
 * never reopened for a second season: next season is a NEW plot, with its own
 * generated code ({supplierCode}-{YYMM}-{running}, round B) and its own QR.
 *
 * The policy is enforced where users meet it — the Excel template offers three
 * actions (create / update / final), and the buttons below are hidden — rather
 * than by deleting the endpoints behind them. Those endpoints still exist and
 * still work, so reversing this decision is a change to THIS file, not a
 * rewrite; and an admin who genuinely needs one can still be given it.
 */

/** Can this plot start a cycle right now?
 *
 * Yes only when it has never had one. That covers the legitimate "reserve the
 * plot today, plan the season later" flow (POST /plots creates a plot with no
 * cycle), while refusing the thing the policy is actually about: starting a
 * SECOND cycle on a plot whose first one is finished.
 *
 * `cycleCount` is the plot's full cycle history, any status — a closed cycle
 * counts, which is the entire point.
 */
export function canStartFirstCycle(cycleCount: number): boolean {
  return cycleCount === 0;
}

/** Can this plot roll over — close its cycle and immediately open another?
 *
 * Never. Rollover exists only to start a second cycle on the same plot, so
 * there is no state in which it is allowed under this policy. Closing a cycle
 * on its own is unaffected (CloseCycleModal), and that is now where the
 * season's actual harvest is recorded (round D).
 */
export function canRolloverCycle(): boolean {
  return false;
}

/** Can a deactivated plot be reopened WITH a fresh cycle?
 *
 * Never — that is reactivate-and-start-another-season, the same thing rollover
 * does for an active plot. Plain reactivation (no cycle) is deliberately still
 * offered: an accidental deactivation must remain undoable, and reopening a
 * plot without starting a cycle breaks no rule.
 */
export function canReactivateWithCycle(): boolean {
  return false;
}
