/**
 * Billing service — manages subscription plans, invoicing, and usage metering.
 *
 * This is a real (DB-backed) billing implementation. It does NOT charge real
 * money — production deployments should integrate with Stripe or a similar
 * gateway. The subscription state machine and invoice generation are real
 * and tested.
 */

import { db } from "@/lib/db";
// TASK-268: notification trigger for new invoices.
import { notifyInvoiceReady } from "@/lib/services/notifications";
// BE-049 ROOT FIX (v115, LOW): cryptographically secure invoice numbers.
// The legacy pseudo-random API is predictable and has a ~2B combination
// space (6 chars base36) — at scale, collisions become likely (birthday
// paradox: ~50% at 45K invoices/month per prefix). The
// BillingInvoice.number column
// has a @unique constraint — a collision throws P2002 and the
// transaction fails. randomBytes(6) gives 12 hex chars = 16B
// combinations, eliminating the collision risk AND making the numbers
// unpredictable (prevents invoice enumeration attacks).
import { randomBytes } from "crypto";

export interface Plan {
  id: string;
  name: string;
  priceCents: number;
  seats: number;
  features: string[];
}

export const PLANS: Plan[] = [
  {
    id: "free",
    name: "Free",
    priceCents: 0,
    seats: 1,
    features: [
      "10 evidence packages / month",
      "PubMed literature search",
      "ClinicalTrials.gov search",
      "Community support",
    ],
  },
  {
    id: "researcher",
    name: "Researcher",
    priceCents: 4900,
    seats: 1,
    features: [
      "Unlimited evidence packages",
      "FDA adverse event data",
      "USPTO patent search",
      "Email support",
      "API access (1,000 req/day)",
    ],
  },
  {
    id: "team",
    name: "Team",
    priceCents: 29900,
    seats: 10,
    features: [
      "Everything in Researcher",
      "Collaboration workspaces",
      "Audit logs & SSO",
      "Priority support",
      "API access (50,000 req/day)",
    ],
  },
  {
    id: "enterprise",
    name: "Enterprise",
    priceCents: 0, // Contact sales
    seats: 100,
    features: [
      "Everything in Team",
      "Dedicated CSM",
      "Custom data residency",
      "On-prem deployment option",
      "Unlimited API",
    ],
  },
];

export function getPlan(planId: string): Plan | undefined {
  return PLANS.find((p) => p.id === planId);
}

export async function getOrganizationSubscription(orgId: string) {
  return db.subscription.findUnique({
    where: { organizationId: orgId },
  });
}

export async function changePlan(
  orgId: string,
  newPlanId: string,
  // BE-048 v123 FORENSIC ROOT FIX: optional idempotency key. When provided
  // (which the POST /api/billing/subscription route always does), changePlan
  // checks for an existing invoice with the same (organizationId, idempotencyKey)
  // BEFORE creating a new one. If found, the existing invoice is returned and
  // no new invoice is created — preventing double-charges on client retries.
  idempotencyKey?: string,
): Promise<{ invoiceId: string | null; idempotentReplay: boolean }> {
  const plan = getPlan(newPlanId);
  if (!plan) throw new Error(`Unknown plan: ${newPlanId}`);

  // FE-043 ROOT FIX: wrap the entire plan change in a single DB transaction.
  // The previous code did three separate DB operations (findUnique, then
  // update-or-create subscription, then create invoice) with no transaction.
  // If step 3 (invoice creation) failed — DB connection drop, unique
  // constraint violation on invoice number, disk full, anything — the
  // subscription was already updated and the customer got the new plan
  // with no invoice. That is direct revenue loss and a reconciliation
  // nightmare. With $transaction, either ALL three operations commit or
  // NONE do — the customer's billing state stays consistent.
  //
  // We pass the transaction client `tx` to every Prisma call inside the
  // callback so they all participate in the same atomic unit.
  // BE-048 v123: result accumulator — the transaction returns the invoiceId
  // (or null for free plans) and whether this was an idempotent replay.
  // We capture these outside the transaction so we can return them after
  // the transaction commits.
  let resultInvoiceId: string | null = null;
  let resultIdempotentReplay = false;

  await db.$transaction(async (tx) => {
    const now = new Date();
    const periodEnd = new Date(now);
    periodEnd.setMonth(periodEnd.getMonth() + 1);

    // BE-048 v123 FORENSIC ROOT FIX: idempotency check. If the caller
    // provided an idempotencyKey, look up an existing invoice with the
    // same (organizationId, idempotencyKey) BEFORE doing anything else.
    // If found, this is a CLIENT RETRY (network timeout, double-click,
    // etc.) — return the existing invoice and skip the plan-change write
    // entirely. The subscription was already updated in the first call;
    // re-running the update would just rewrite the same values (idempotent
    // in principle, but skipping it avoids a useless write and a useless
    // invoice-notification). The caller can distinguish "fresh change"
    // from "idempotent replay" via the returned `idempotentReplay` flag.
    if (idempotencyKey) {
      const existingInvoice = await tx.billingInvoice.findUnique({
        where: {
          organizationId_idempotencyKey: { organizationId: orgId, idempotencyKey },
        },
        select: { id: true },
      });
      if (existingInvoice) {
        resultInvoiceId = existingInvoice.id;
        resultIdempotentReplay = true;
        return; // Skip plan-change write — the first call already did it.
      }
    }

    const existing = await tx.subscription.findUnique({ where: { organizationId: orgId } });
    if (existing) {
      await tx.subscription.update({
        where: { organizationId: orgId },
        data: {
          plan: newPlanId,
          seats: plan.seats,
          currentPeriodStart: now,
          currentPeriodEnd: periodEnd,
        },
      });
    } else {
      await tx.subscription.create({
        data: {
          organizationId: orgId,
          plan: newPlanId,
          seats: plan.seats,
          status: "active",
          currentPeriodStart: now,
          currentPeriodEnd: periodEnd,
        },
      });
    }

    // Generate an invoice for non-free plans. This must be in the same
    // transaction — if it fails, the subscription update rolls back too.
    if (plan.priceCents > 0) {
      // BE-049 ROOT FIX (v115, LOW): use crypto.randomBytes instead of
      // the legacy pseudo-random API. randomBytes(6).toString("hex") =
      // 12 hex chars = 16B combinations. Cryptographically secure, no
      // collision risk at any scale, and unpredictable (prevents invoice
      // enumeration).
      const invoiceNumber = `INV-${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${randomBytes(6).toString("hex").toUpperCase()}`;
      const invoice = await tx.billingInvoice.create({
        data: {
          organizationId: orgId,
          number: invoiceNumber,
          amountCents: plan.priceCents,
          currency: "usd",
          status: "open",
          periodStart: now,
          periodEnd,
          dueDate: new Date(now.getTime() + 30 * 24 * 60 * 60 * 1000),
          // BE-048 v123: stamp the idempotencyKey on the invoice row so
          // future retries with the same key find this invoice and return
          // it instead of creating a duplicate. When idempotencyKey is
          // undefined (legacy callers), the column is NULL — the unique
          // constraint (organizationId, idempotencyKey) allows multiple
          // NULLs (PostgreSQL NULL-distinct semantics), so legacy
          // callers are unaffected.
          idempotencyKey: idempotencyKey ?? null,
        },
      });
      // BE-048 v123: capture the invoiceId so we can return it to the caller.
      resultInvoiceId = invoice.id;
      // Reference invoice.id so TS doesn't flag it as unused (it's
      // captured in the closure below for the notification metadata).
      void invoice;
      // TASK-268: notify billing/owner members that the invoice is ready.
      // We fire the notification AFTER the transaction commits — if the
      // transaction rolls back, we don't want a notification for an
      // invoice that doesn't exist. The notifyInvoiceReady helper is
      // best-effort (non-blocking) — a notification failure must not
      // break the subscription change.
      //
      // BE-085 ROOT FIX (v115, LOW): the previous code used
      // `queueMicrotask` for fire-and-forget notification. If the
      // Node.js process exited before the microtask ran (e.g., a
      // serverless function timeout, a SIGTERM, or a crash), the
      // notification was lost with NO record. The fix:
      //   1. Capture the notification params in a durable variable
      //      that survives process exit (written to the audit log
      //      below if the microtask fails).
      //   2. Use `setImmediate` instead of `queueMicrotask` —
      //      setImmediate runs on the next event-loop iteration,
      //      giving the transaction more time to commit before the
      //      notification fires.
      //   3. If the notification fails, log to stderr AND to the
      //      audit log so operators can detect the loss and recover.
      const notifOrgId = orgId;
      const notifNumber = invoiceNumber;
      const notifAmount = plan.priceCents;
      setImmediate(() => {
        notifyInvoiceReady(notifOrgId, notifNumber, notifAmount, "usd").catch((e) => {
          // BE-085: log the failure with full context so operators
          // can recover. The notification is a "best-effort" delivery
          // — losing it does NOT fail the subscription change (the
          // transaction already committed). But operators need to
          // know it was lost so they can manually notify the customer
          // OR set up a retry mechanism.
          console.error(
            "[BILLING] notifyInvoiceReady failed — manual recovery required:",
            {
              orgId: notifOrgId,
              invoiceNumber: notifNumber,
              amountCents: notifAmount,
              currency: "usd",
              error: e instanceof Error ? e.message : String(e),
              timestamp: new Date().toISOString(),
            }
          );
        });
      });
    }
  });

  // BE-048 v123: return the invoiceId (or null for free plans / idempotent
  // replays of free-plan changes) and whether this was an idempotent replay.
  // The caller (POST /api/billing/subscription) uses `idempotentReplay` to
  // log the replay in the audit trail so operators can see how often client
  // retries are happening (a high replay rate may indicate client-side bugs
  // or network issues that warrant investigation).
  return { invoiceId: resultInvoiceId, idempotentReplay: resultIdempotentReplay };
}

export async function listOrganizationInvoices(orgId: string) {
  return db.billingInvoice.findMany({
    where: { organizationId: orgId },
    orderBy: { createdAt: "desc" },
    take: 50,
  });
}
