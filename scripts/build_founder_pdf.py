"""Generate the founder-facing product + business update PDF.

Run: python scripts/build_founder_pdf.py
Output: docs/Arteq_Arya_Founder_Update.pdf

Uses fpdf2 with core fonts (latin-1), so the rupee symbol is written as "Rs".
"""
from fpdf import FPDF

# ---- palette -------------------------------------------------------------
INK = (33, 37, 41)
MUTED = (108, 117, 125)
BRAND = (13, 110, 168)       # teal-blue
BRAND_DK = (9, 74, 112)
ACCENT = (32, 161, 120)      # green
WARN = (201, 122, 18)        # amber
LIGHT = (236, 244, 249)
LIGHT2 = (245, 248, 250)
LINE = (210, 218, 224)
WHITE = (255, 255, 255)

RS = "Rs "


class PDF(FPDF):
    def header(self):
        if self.page_no() == 1:
            return
        self.set_y(8)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 5, "Arteq AI  |  Arya - Founder Update", align="L")
        self.cell(0, 5, "Confidential", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*LINE)
        self.line(self.l_margin, 15, self.w - self.r_margin, 15)

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, f"Page {self.page_no()}", align="C")


pdf = PDF(orientation="P", unit="mm", format="A4")
pdf.set_auto_page_break(auto=True, margin=16)
pdf.set_margins(18, 18, 18)
EPW = pdf.w - pdf.l_margin - pdf.r_margin  # effective page width


# ---- helpers -------------------------------------------------------------
def h1(txt):
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*BRAND_DK)
    pdf.multi_cell(0, 8, txt)
    pdf.set_draw_color(*BRAND)
    pdf.set_line_width(0.6)
    y = pdf.get_y() + 1
    pdf.line(pdf.l_margin, y, pdf.l_margin + 26, y)
    pdf.set_line_width(0.2)
    pdf.ln(4)


def h2(txt):
    pdf.ln(1)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*INK)
    pdf.multi_cell(0, 6, txt)
    pdf.ln(1)


def body(txt, color=INK):
    pdf.set_font("Helvetica", "", 10.5)
    pdf.set_text_color(*color)
    pdf.multi_cell(0, 5.4, txt)
    pdf.ln(1.5)


def bullet(txt, label=None):
    pdf.set_font("Helvetica", "", 10.5)
    pdf.set_text_color(*INK)
    pdf.set_text_color(*ACCENT)
    pdf.cell(5, 5.2, chr(149))  # bullet dot
    pdf.set_text_color(*INK)
    if label:
        pdf.set_font("Helvetica", "B", 10.5)
        lw = pdf.get_string_width(label + " ")
        pdf.cell(lw, 5.2, label + " ")
        pdf.set_font("Helvetica", "", 10.5)
        pdf.multi_cell(EPW - 5 - lw, 5.2, txt)
    else:
        pdf.multi_cell(EPW - 5, 5.2, txt)
    pdf.ln(0.6)


def table(headers, rows, widths, aligns=None, header_bg=BRAND, zebra=True,
          font_size=9.5):
    aligns = aligns or ["L"] * len(headers)
    pdf.set_font("Helvetica", "B", font_size)
    pdf.set_fill_color(*header_bg)
    pdf.set_text_color(*WHITE)
    pdf.set_draw_color(*LINE)
    for i, hd in enumerate(headers):
        pdf.cell(widths[i], 8, hd, border=0, align=aligns[i], fill=True)
    pdf.ln(8)
    pdf.set_font("Helvetica", "", font_size)
    pdf.set_text_color(*INK)
    for r, row in enumerate(rows):
        fill = zebra and (r % 2 == 0)
        if fill:
            pdf.set_fill_color(*LIGHT2)
        # compute row height by wrapping check (assume single line for these)
        for i, cell in enumerate(row):
            bold = cell.startswith("*")
            text = cell[1:] if bold else cell
            pdf.set_font("Helvetica", "B" if bold else "", font_size)
            pdf.cell(widths[i], 7, text, border="B", align=aligns[i],
                     fill=fill)
        pdf.ln(7)
    pdf.ln(2)


def callout(title, txt, bg=LIGHT, bar=BRAND):
    pdf.ln(1)
    x0 = pdf.l_margin
    y0 = pdf.get_y()
    pdf.set_font("Helvetica", "B", 10.5)
    th = 6
    pdf.set_font("Helvetica", "", 10)
    # estimate height
    lines = pdf.multi_cell(EPW - 10, 5, txt, dry_run=True, output="LINES")
    bh = th + len(lines) * 5 + 6
    pdf.set_fill_color(*bg)
    pdf.rect(x0, y0, EPW, bh, "F")
    pdf.set_fill_color(*bar)
    pdf.rect(x0, y0, 1.8, bh, "F")
    pdf.set_xy(x0 + 6, y0 + 3)
    pdf.set_font("Helvetica", "B", 10.5)
    pdf.set_text_color(*bar)
    pdf.cell(0, 5, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(x0 + 6)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*INK)
    pdf.multi_cell(EPW - 10, 5, txt)
    pdf.set_y(y0 + bh + 3)


# =====================================================================
# COVER
# =====================================================================
pdf.add_page()
pdf.set_fill_color(*BRAND_DK)
pdf.rect(0, 0, pdf.w, 74, "F")
pdf.set_fill_color(*ACCENT)
pdf.rect(0, 74, pdf.w, 2.5, "F")
pdf.set_xy(18, 22)
pdf.set_font("Helvetica", "B", 30)
pdf.set_text_color(*WHITE)
pdf.cell(0, 14, "Arya by Arteq AI", new_x="LMARGIN", new_y="NEXT")
pdf.set_x(18)
pdf.set_font("Helvetica", "", 14)
pdf.cell(0, 9, "The AI Voice Receptionist for Hospitals & Clinics",
         new_x="LMARGIN", new_y="NEXT")
pdf.set_x(18)
pdf.set_font("Helvetica", "B", 11)
pdf.set_text_color(180, 220, 240)
pdf.cell(0, 8, "Founder Update  -  What we have built, and the business behind it")

pdf.set_xy(18, 90)
pdf.set_font("Helvetica", "", 11)
pdf.set_text_color(*MUTED)
pdf.cell(0, 6, "Prepared June 2026   |   All figures in Indian Rupees (shown as 'Rs').   |   Prices exclude 18% GST.",
         new_x="LMARGIN", new_y="NEXT")

pdf.set_xy(18, 104)
callout(
    "In one sentence",
    "Arya answers every hospital phone call in Malayalam and 5 other languages, "
    "24x7, on every line at once - she books appointments, confirms them on "
    "WhatsApp, manages the patient queue, alerts staff for emergencies, and "
    "never puts a caller on hold.",
    bg=LIGHT, bar=ACCENT,
)

pdf.ln(2)
h2("What's inside")
for n, t in [
    ("1", "What we shipped - the full product, in plain language"),
    ("2", "How it works - workflow from every point of view"),
    ("3", "Pricing plans"),
    ("4", "What it cost us to build (fixed costs)"),
    ("5", "What it costs to run, per minute"),
    ("6", "Ways we can deliver the service, and their cost"),
    ("7", "Expected monthly recurring revenue (MRR)"),
    ("8", "Where we stand & what's next"),
]:
    pdf.set_font("Helvetica", "B", 10.5)
    pdf.set_text_color(*BRAND)
    pdf.cell(7, 6, n)
    pdf.set_font("Helvetica", "", 10.5)
    pdf.set_text_color(*INK)
    pdf.cell(0, 6, t, new_x="LMARGIN", new_y="NEXT")


# =====================================================================
# 1. WHAT WE SHIPPED
# =====================================================================
pdf.add_page()
h1("1. What we shipped")
body(
    "Arya is live and feature-complete. Below is everything she can do today, "
    "in plain language. Each item is fully working, not a plan.")

h2("Answering & conversation")
bullet("Arya speaks Malayalam, Hindi, Tamil, Telugu, Kannada and English, and "
       "switches to the caller's language automatically.", "6 languages.")
bullet("She picks up instantly and handles unlimited calls at the same time - "
       "no hold music, no queue, no missed calls after hours.", "Always on.")
bullet("She sounds natural and replies fast. Common phrases are pre-voiced and "
       "reused, so there is no robotic delay.", "Human-like.")

h2("Appointments & the patient queue")
bullet("Patients can book, reschedule or cancel an appointment entirely by "
       "voice. Arya knows doctors, departments, and timings.", "Self-service booking.")
bullet("Every booking gets a short confirmation code (e.g. ARYA-7K2P) the "
       "patient can quote at the desk.", "Confirmation code.")
bullet("The patient pays the fee at the hospital; the moment staff mark it "
       "paid, Arya activates the patient's queue token and messages them the "
       "token number. No payment, no token - clean and fair.", "Pay-then-token.")
bullet("Emergencies and senior citizens are automatically pushed up the queue, "
       "so the most urgent patients are seen first.", "Smart priority.")
bullet("If a department has several doctors, Arya routes the patient to the "
       "least-busy one, balancing the load across the team.", "Load balancing.")
bullet("Two patients grabbing the same slot at the same instant can never "
       "double-book - the system locks the slot safely.", "No double-booking.")

h2("Messaging & staff alerts")
bullet("Confirmations and reminders go out on WhatsApp, with an automatic SMS "
       "fallback if WhatsApp can't be delivered.", "WhatsApp-first.")
bullet("Staff get an instant alert for new bookings, cancellations, and "
       "especially emergencies, so a human is always in the loop.", "Staff alerts.")
bullet("Arya can transfer a live call to a human staff member when the caller "
       "asks or when she judges it necessary.", "Live transfer.")

h2("Automatic follow-ups (runs on its own)")
bullet("Appointment reminders before the visit, confirmation requests, missed-"
       "call callbacks, and post-visit follow-ups - all sent automatically on a "
       "schedule, with no staff effort.", "Outbound engine.")

h2("Dashboard & multi-hospital")
bullet("A web dashboard shows call logs, bookings, costs, and live activity, "
       "protected by secure staff login.", "Owner dashboard.")
bullet("One system runs many hospitals and clinics at once, each with its own "
       "doctors, persona name, and data kept private and separate.", "Multi-tenant.")
bullet("Hospital data stays isolated per tenant and access is authenticated - "
       "built with patient privacy in mind.", "Private & secure.")

h2("Deployment (how we ship it)")
bullet("Arya can run on managed cloud for a fast launch, or be fully self-"
       "hosted on a single low-cost server we control - same quality, lower "
       "cost. Both are packaged in containers, so setup is repeatable.",
       "Two ways to run.")


# =====================================================================
# 2. WORKFLOW DIAGRAM (multi-POV)
# =====================================================================
pdf.add_page()
h1("2. How it works - from every point of view")
body("The same call touches four groups. Here is what each one sees and does.")


# ---- diagram geometry (all inside page margins) -------------------------
LBL_W = 32                       # actor label column width
BODY_X = pdf.l_margin + LBL_W    # where lane body starts
BODY_W = EPW - LBL_W
PAD = 3
BW = 23.5                        # step box width
GAPX = 3.6
BH = 17                          # step box height
BX0 = BODY_X + PAD
COL = lambda i: BX0 + i * (BW + GAPX)   # col(4) right edge = 187.4 < 192 OK
N_COLS = 5
STAGES = ["1 - Call in", "2 - Match", "3 - Book", "4 - Manage", "5 - Visit"]


def rrect(x, y, w, h, style, fill=None, draw=None, r=2.2):
    if fill:
        pdf.set_fill_color(*fill)
    if draw:
        pdf.set_draw_color(*draw)
    pdf.rect(x, y, w, h, style=style, round_corners=True, corner_radius=r)


def lane(y, h, color, title, who):
    x = pdf.l_margin
    # body first (so label sits crisp on top edge)
    rrect(x + LBL_W, y, BODY_W, h, "F", fill=LIGHT2, r=1.5)
    pdf.set_draw_color(*LINE)
    pdf.set_line_width(0.2)
    rrect(x + LBL_W, y, BODY_W, h, "D", draw=LINE, r=1.5)
    # faint column dividers
    pdf.set_draw_color(225, 231, 236)
    for i in range(1, N_COLS):
        gx = (COL(i - 1) + BW + COL(i)) / 2
        pdf.line(gx, y + 2, gx, y + h - 2)
    # label block
    rrect(x, y, LBL_W, h, "F", fill=color, r=1.5)
    pdf.set_xy(x + 1.5, y + h / 2 - 6.5)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.set_text_color(*WHITE)
    pdf.multi_cell(LBL_W - 3, 4.4, title, align="C")
    pdf.set_xy(x + 1.5, y + h / 2 + 2.5)
    pdf.set_font("Helvetica", "", 7)
    pdf.multi_cell(LBL_W - 3, 3.2, who, align="C")


def step_box(ci, cy, text, fill):
    cx = COL(ci)
    # drop shadow
    pdf.set_fill_color(208, 214, 219)
    pdf.rect(cx + 0.7, cy + 0.9, BW, BH, style="F", round_corners=True,
             corner_radius=2)
    rrect(cx, cy, BW, BH, "F", fill=fill, r=2)
    pdf.set_font("Helvetica", "B", 7.4)
    pdf.set_text_color(*WHITE)
    lines = pdf.multi_cell(BW, 3.4, text, align="C", dry_run=True,
                           output="LINES")
    ty = cy + (BH - len(lines) * 3.4) / 2
    pdf.set_xy(cx, ty)
    pdf.multi_cell(BW, 3.4, text, align="C")


def harrow(ci_from, y, ci_to):
    x1 = COL(ci_from) + BW
    x2 = COL(ci_to)
    pdf.set_draw_color(*MUTED)
    pdf.set_line_width(0.5)
    pdf.line(x1 + 0.5, y, x2 - 2, y)
    pdf.line(x2 - 2.3, y - 1.3, x2 - 0.3, y)
    pdf.line(x2 - 2.3, y + 1.3, x2 - 0.3, y)
    pdf.set_line_width(0.2)


def varrow(ci, y1, y2, color=INK):
    x = COL(ci) + BW / 2
    pdf.set_draw_color(*color)
    pdf.set_line_width(0.5)
    pdf.line(x, y1, x, y2 - 1.8)
    pdf.line(x - 1.3, y2 - 2.1, x, y2 - 0.3)
    pdf.line(x + 1.3, y2 - 2.1, x, y2 - 0.3)
    pdf.set_line_width(0.2)


lanes = [
    (BRAND, "PATIENT", "the caller"),
    (ACCENT, "ARYA (AI)", "our software"),
    (WARN, "HOSPITAL", "doctors & staff"),
    (BRAND_DK, "ARTEQ AI", "us"),
]
lane_h = 24
gap = 4

# column stage headers
top = pdf.get_y() + 8
pdf.set_font("Helvetica", "B", 7.8)
pdf.set_text_color(*BRAND_DK)
for i in range(N_COLS):
    pdf.set_xy(COL(i), top - 6.5)
    pdf.cell(BW, 5, STAGES[i], align="C")

ys = []
for i, (c, t, w) in enumerate(lanes):
    y = top + i * (lane_h + gap)
    ys.append(y)
    lane(y, lane_h, c, t, w)

bycen = lambda li: ys[li] + (lane_h - BH) / 2
mid = lambda li: ys[li] + lane_h / 2

# PATIENT
py = bycen(0)
step_box(0, py, "Calls the\nhospital\nnumber", BRAND)
step_box(2, py, "Speaks:\n'book Dr X\ntomorrow'", BRAND)
step_box(4, py, "Gets token\non WhatsApp", BRAND)
harrow(0, mid(0), 2)
harrow(2, mid(0), 4)

# ARYA
ay = bycen(1)
step_box(0, ay, "Answers in\nMalayalam", ACCENT)
step_box(1, ay, "Finds doctor\n+ free slot", ACCENT)
step_box(2, ay, "Books slot,\nsends code", ACCENT)
step_box(3, ay, "Alerts staff,\nruns queue", ACCENT)
step_box(4, ay, "Activates\ntoken when\npaid", ACCENT)
for i in range(4):
    harrow(i, mid(1), i + 1)

# HOSPITAL
hy = bycen(2)
step_box(1, hy, "Doctors &\nslots loaded\nonce", WARN)
step_box(3, hy, "Staff confirm\npayment", WARN)
step_box(4, hy, "Patient seen,\nno missed\ncalls", WARN)

# ARTEQ
qy = bycen(3)
step_box(0, qy, "Onboard,\nset up\nnumber", BRAND_DK)
step_box(2, qy, "Run on cloud\nor our server", BRAND_DK)
step_box(4, qy, "Bill monthly,\nmonitor", BRAND_DK)

# cross-lane handoffs
varrow(0, py + BH, ay, color=BRAND)            # patient call -> Arya
varrow(0, qy, ay + BH, color=BRAND_DK)         # Arteq setup -> Arya
varrow(1, hy, ay + BH, color=WARN)             # hospital data -> Arya
varrow(2, ay + BH, hy, color=ACCENT)           # Arya book -> hospital
varrow(4, ay + BH, hy, color=ACCENT)           # Arya -> hospital (visit)
varrow(4, ay, py + BH, color=ACCENT)           # Arya token -> patient

pdf.set_y(ys[3] + lane_h + 5)
callout(
    "Read it like this",
    "Each row is one group; time runs left to right across the 5 stages on top. "
    "The patient just talks. Arya (green) does all the work in the middle. The "
    "hospital only loads its doctors once and confirms payments. We (Arteq) set "
    "it up, run it, and bill a simple monthly fee. Arrows show the call moving "
    "between them.",
    bg=LIGHT, bar=BRAND,
)


# =====================================================================
# 3. PRICING PLANS
# =====================================================================
pdf.add_page()
h1("3. Pricing plans")
body("Each plan is a monthly subscription with a block of included talk-time. "
     "Extra minutes are billed at the plan rate + Rs 0.50/min.")
table(
    ["Plan", "Included", "Monthly", "Rs/min", "Best for"],
    [
        ["*Starter", "1,000 min", RS + "6,999", "7.00", "Single-doctor clinics"],
        ["*Growth", "2,500 min", RS + "14,999", "6.00", "Small clinics"],
        ["*Professional", "30,000 min", RS + "1,34,999", "4.50", "Mid-size hospitals"],
        ["*Enterprise", "60,000 min", RS + "2,39,999", "4.00", "Large hospitals"],
        ["*Enterprise+", "100,000 min", RS + "3,49,999", "3.50", "Multi-specialty groups"],
    ],
    widths=[30, 24, 32, 18, EPW - 104],
    aligns=["L", "R", "R", "R", "L"],
)
body("Every plan includes: 6 languages, 24x7 answering, unlimited simultaneous "
     "calls, voice booking, WhatsApp + SMS confirmations, queue tokens, priority "
     "& load balancing, live transfer, staff alerts, and the owner dashboard.")
callout(
    "Add-ons",
    "One-time setup (number, data load, staff training): Rs 10,000 - 25,000.   "
    "Optional 14-day free trial capped at ~300 min (our cost under Rs 900).   "
    "All prices exclude 18% GST.",
    bg=LIGHT2, bar=WARN,
)


# =====================================================================
# 4. FIXED / BUILD COSTS
# =====================================================================
pdf.add_page()
h1("4. What it cost us to build")
body("These are the fixed and tooling costs to design, build and stand up Arya. "
     "Most are monthly subscriptions during the build; the engineering itself "
     "was done in-house.")
table(
    ["Item", "What it's for", "Cost"],
    [
        ["*Claude Code (AI dev)", "Building the whole product", RS + "1,500-1,800/mo"],
        ["*Groq API", "The AI 'brain' (reasoning)", "Pay-per-use, ~" + RS + "0.20/min"],
        ["*Sarvam API", "Speech-to-text + voice (Indian langs)", "Pay-per-use, ~" + RS + "0.65/min"],
        ["*LiveKit", "Voice call infrastructure (open-source)", "Free self-host / cloud per-min"],
        ["*Plivo", "Phone numbers + WhatsApp/SMS", "~" + RS + "0.60/min + number rent"],
        ["*Supabase", "Database (patient & booking data)", "Free tier -> " + RS + "~2,000/mo"],
        ["*VPS (Hostinger)", "Server to run Arya", RS + "799/mo (renews ~1,400)"],
        ["*Domain + misc", "Web address, certificates", RS + "~1,000/yr"],
    ],
    widths=[42, EPW - 42 - 42, 42],
    aligns=["L", "L", "R"],
    font_size=9,
)
callout(
    "The key point for cost",
    "Almost everything is pay-as-you-go. We don't pay for idle capacity - the "
    "big costs (Groq, Sarvam, Plivo) are only charged per minute Arya is "
    "actually on a call. Fixed monthly spend during build is roughly "
    "Rs 5,000 - 6,000, mostly tooling.",
    bg=LIGHT, bar=ACCENT,
)


# =====================================================================
# 5. RUN COST PER MINUTE
# =====================================================================
h1("5. What it costs to run, per minute")
body("Every minute Arya is on a call, we pay these providers. We run two setups "
     "and move customers to the cheaper self-hosted one as volume grows.")
table(
    ["Cost component", "Managed Cloud", "Self-hosted VPS"],
    [
        ["Phone line (Plivo)", RS + "0.60", RS + "0.60"],
        ["Voice infra (LiveKit)", RS + "1.20", "~" + RS + "0.10"],
        ["Speech understanding (Sarvam)", RS + "0.50", RS + "0.50"],
        ["Speech voice (Sarvam)", RS + "0.15", RS + "0.15"],
        ["AI brain (Groq)", RS + "0.20", RS + "0.20"],
        ["SMS / WhatsApp + buffer", RS + "0.10", RS + "0.10"],
        ["*Total per minute", "*~" + RS + "2.75", "*~" + RS + "1.65"],
    ],
    widths=[EPW - 80, 40, 40],
    aligns=["L", "R", "R"],
    font_size=9.5,
)
body("Self-hosting drops the voice-infra cost from Rs 1.20 to a few paise "
     "because one Rs 799 server is shared across many clinics (multi-tenant). "
     "Quality is identical - only where LiveKit runs changes.")


# =====================================================================
# 6. DELIVERY OPTIONS
# =====================================================================
pdf.add_page()
h1("6. Ways we can deliver the service")
body("Three delivery models, same product. They trade setup speed against "
     "running cost and margin.")
table(
    ["Option", "What it is", "Our cost/min", "Best when"],
    [
        ["*Managed Cloud", "LiveKit's rented cloud; fastest launch",
         "~" + RS + "2.75", "First customers, pilots"],
        ["*Self-hosted VPS", "LiveKit on our Rs 799 box, multi-tenant",
         "~" + RS + "1.65", "Default at scale"],
        ["*Dedicated VPS", "One hospital's own box + standby failover",
         "~" + RS + "1.85", "Large hospital wanting isolation"],
    ],
    widths=[34, EPW - 34 - 34 - 38, 34, 38],
    aligns=["L", "L", "R", "L"],
    font_size=9,
)
callout(
    "Our recommendation",
    "Launch new customers on Managed Cloud for a same-day start, then migrate "
    "them onto a shared self-hosted box once their volume is steady. This keeps "
    "onboarding instant while protecting our ~50% margin. Offer a dedicated box "
    "(with a standby for failover) only to large hospitals that ask for it.",
    bg=LIGHT, bar=BRAND,
)
body("Per-plan margin stays near 50% across the board: small plans run on cloud "
     "(higher cost, higher price), large plans on our own infra (lower cost).")
table(
    ["Plan", "Price", "Our cost", "Gross profit", "Margin"],
    [
        ["Starter", RS + "6,999", "~" + RS + "3,000", "*" + RS + "3,999", "57%"],
        ["Growth", RS + "14,999", "~" + RS + "7,500", "*" + RS + "7,499", "50%"],
        ["Professional", RS + "1,34,999", "~" + RS + "66,000", "*" + RS + "68,999", "51%"],
        ["Enterprise", RS + "2,39,999", "~" + RS + "1,20,000", "*" + RS + "1,19,999", "50%"],
        ["Enterprise+", RS + "3,49,999", "~" + RS + "1,80,000", "*" + RS + "1,69,999", "49%"],
    ],
    widths=[34, 34, 34, 38, EPW - 140],
    aligns=["L", "R", "R", "R", "R"],
    font_size=9,
)


# =====================================================================
# 7. EXPECTED MRR
# =====================================================================
pdf.add_page()
h1("7. Expected monthly recurring revenue (MRR)")
body("MRR is the predictable subscription income each month. Below are three "
     "scenarios for the first 12 months, using a realistic Kerala customer mix. "
     "'Gross profit' uses our ~50% blended margin.")

h2("A realistic customer mix")
body("Most clinics land in Starter/Growth; most private hospitals in "
     "Professional/Enterprise. We model a blended average revenue per customer "
     "of about Rs 22,000/month across this mix.")

table(
    ["Scenario (by month 12)", "Customers", "Avg/customer", "MRR", "Gross profit/mo"],
    [
        ["*Conservative", "10", RS + "22,000", "*" + RS + "2.2 L", "~" + RS + "1.1 L"],
        ["*Base case", "25", RS + "22,000", "*" + RS + "5.5 L", "~" + RS + "2.75 L"],
        ["*Aggressive", "50", RS + "24,000", "*" + RS + "12.0 L", "~" + RS + "6.0 L"],
    ],
    widths=[44, 26, 30, 30, EPW - 130],
    aligns=["L", "R", "R", "R", "R"],
    font_size=9,
)
body("L = lakh (Rs 1,00,000).  Annualised, the base case is roughly Rs 66 lakh "
     "ARR with about Rs 33 lakh gross profit, before salaries and marketing.")

h2("Why this is reachable")
bullet("Kerala has thousands of clinics and hundreds of private hospitals - the "
       "target market is large and underserved on phone handling.")
bullet("Each closed customer is sticky: once Arya holds their doctors, queue and "
       "history, switching cost is high.")
bullet("Margin holds at ~50% even as we grow, because larger customers run on "
       "our cheaper self-hosted infrastructure.")
bullet("A single Rs 799 server can carry 20-40 small clinics, so adding "
       "customers barely adds cost.")

callout(
    "The simple pitch",
    '"One missed call can be one lost patient. Arya answers every call, in '
    'Malayalam, day and night, and books the appointment on the spot - for less '
    'than the cost of a single receptionist."',
    bg=LIGHT, bar=ACCENT,
)


# =====================================================================
# 8. STATUS & NEXT
# =====================================================================
h1("8. Where we stand & what's next")
h2("Done")
bullet("Full product built and working: answering, booking, WhatsApp tokens, "
       "priority queue, load balancing, alerts, dashboard, multi-hospital.")
bullet("Both delivery setups packaged (managed cloud + one-server self-host).")
h2("To go fully live")
bullet("Point the hospital's phone number/WhatsApp line at Arya (one-time setup).")
bullet("Load the first hospital's doctors, departments and timings.")
bullet("Run the first paid pilot and confirm real-world call quality and cost.")

pdf.ln(2)
pdf.set_font("Helvetica", "", 9)
pdf.set_text_color(*MUTED)
pdf.multi_cell(0, 4.6,
    "Notes: Figures are indicative, based on June 2026 provider rates - revisit "
    "if Plivo / LiveKit / Sarvam / Groq pricing changes. Add 18% GST on all "
    "customer prices. MRR scenarios assume the stated customer counts and the "
    "blended average revenue per customer; actual results depend on sales pace.")

# ---- output --------------------------------------------------------------
import os
os.makedirs("docs", exist_ok=True)
out = os.path.join("docs", "Arteq_Arya_Founder_Update.pdf")
pdf.output(out)
print("WROTE", out)
