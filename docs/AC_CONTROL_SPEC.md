# AC (Ventilation) Control Specification

Status: draft for review, 2026-10-12. Only the interim protection
(section 8) is built, on 2026-10-14.

## 1. Purpose

The "AC" is the house supply and exhaust ventilation. A Shelly relay at
192.168.1.5 (relay/0) switches it. The code inherited from wibatemp calls
it the "AC pump" or the "circulation pump"; that name is wrong and goes.

The hazard: when the AC starts, the exhaust fan starts first and the
supply fan about a minute later. In that minute the house is under
negative pressure. If the wood-fired oven (leivinuuni) fire is still
starting, replacement air comes down the chimney, and thick smoke comes
into the house and spreads everywhere. This has happened a couple of
times.

So any code that turns the AC on is safety-relevant.

## 2. What switched the AC before the interim protection

| Source | Off | On |
|---|---|---|
| Program EVU block (executor) | at the block start, from ON or ALE | at the block end, to ON or ALE |
| Boot restore | for a due EVU | for ON and ALE |
| Manual `redhouse-pump ON/ALE/EVU` | EVU from ON or ALE | ON or ALE from EVU |
| Periodic EVU pulse | never | never |
| Shelly UI, by hand | yes | yes |
| Shelly power-on setting | after a power cut | after a power cut |

The executor tried the Shelly once and ignored a failure. The boot
restore retried 7 times and failed the unit. With the interim
protection (section 8), only the last two rows remain.

## 3. Rules

R1. Fire window. The user marks a firing. From the mark, redhouse never
switches the AC, in either direction. If the AC is on, it stays on. If
it is off, it stays off. The window has the highest precedence. It
ends at the first of these:

- at least 60 minutes after the mark, when the Savupiippu sensor shows
  an established fire: 1 C or more above its level before the mark,
  and still rising;
- 90 minutes after the mark, if the Savupiippu sensor gives no valid
  data;
- 3 hours after the mark in any case.
A new mark starts the window again. `redhouse-pump fire end` ends it
early. That is at the user's own risk, and the command says so before
it acts. Reason for the length: on 2026-10-12 the fire weakened 15-20
minutes after lighting and needed an open door, and it drew well only
after about 55 minutes. The confirmed restart backdrafts came 34 and
44 minutes after lighting (section 10). A cold oven, as at the first
fires of the season, is the most sensitive start. Those fires are kept
small, so the chimney warms slowly; the sensor-based end then extends
the window by itself.

R2. Pump commands never touch the AC. `redhouse-pump ON|ALE|EVU`, the
program's pump entries and the periodic EVU pulse set only the I2C pump
relays.

R3. Manual AC control. `redhouse-pump ac on|off` sets the AC and clears
the "off by redhouse" record (R6). A manual `ac on` also ends the active
holds: the user has judged that it is safe. `redhouse-pump ac status`
reads the Shelly and the AC state. During a fire window, `ac on` and
`ac off` are refused with a message that names the window and its end
time.

R4. Smoke hold. When the smoke rule (section 6) detects thick smoke from
the neighbour's chimney, the AC holds off for 15 minutes. The smoke has
usually cleared by then. The duration is a setting, to be tuned by
experiment, including whether a new alert extends the hold.

R5. EVU hold. A program EVU block holds the AC off from the block start
to the block end.

R6. Ownership. Redhouse only undoes its own off:
- To start a hold, redhouse turns the AC off only if the Shelly reports
  it on. It then records "off by redhouse".
- When no hold is active and no fire window runs, redhouse turns the AC
  on only if the record says "off by redhouse". It then clears it.
- A manual off (Shelly UI or `ac off`) stays off until the user turns
  the AC on.

R7. Precedence: fire window (R1) > manual command (R3) > smoke hold (R4)
> EVU hold (R5). A hold that starts during a fire window does nothing
until the window ends. Then the normal rules apply at the next check.

R8. Boot restore. It applies R6 from the program's due entry: off during
a due EVU block, otherwise on only if "off by redhouse". If the clock is
not synchronized, or no due entry is known, it does not touch the AC. A
fire window whose end it cannot check counts as active.

R9. Failure. Every AC switch retries 7 times, 10 s apart. A switch that
still fails is logged, reported as a failed command, and shows in the
health check.

R10. Notification. The start of a smoke hold and a refused AC command
send an email through the existing alert mail, with its repeat
suppression.

R11. Overpressure in fire mode. The ventilation's "Ylipaine" wall
switch sets more supply than exhaust air, the opposite of the hazard.
A second Shelly relay, wired in parallel with the wall switch, lets
redhouse set it: on at the fire mark, off when the fire window ends.
In parallel, either the wall switch or the relay turns overpressure
on, so the wall switch keeps working if redhouse or the relay fails.
Redhouse turns off only the overpressure it turned on.

## 4. State

A new file, data/ac_state.json, separate from the pump state:

- fire_until: epoch seconds, or null
- holds: a list of {reason: "evu" | "smoke", until: epoch seconds}
- off_by_redhouse: true or false
- last_switch: {state: "on" | "off", time, by: "evu" | "smoke" |
  "manual" | "restore"}

It is written atomically, like the pump state. A missing or unreadable
file reads as: no fire window, no holds, off_by_redhouse false. That is
the safe default, because nothing is then turned on.

The AC state, the fire window and the holds also go to InfluxDB, so
Grafana and the e-ink display can show them.

## 5. When the rules run

- Executor, every 15 minutes: it adds or ends the EVU hold, then applies
  R6 and R7.
- Smoke service, about once a second: it adds or extends the smoke hold
  and applies R6 and R7 at once.
- Fire command: it sets fire_until and switches nothing.
- Boot restore: R8.

## 6. Smoke detection

Sensor: NT-HL long range laser distance sensor, RS-485 Modbus RTU, 40 m
range. It is aimed from the roof past the top of the neighbour's chimney.
Thick smoke gives a shorter distance, or no valid reading.

Wiring: the spare Cat6 run from the basement to upstairs, then a short
outdoor-rated cable to the roof. Suggested pairs (T568B): blue (4/5)
RS-485 A and B, brown (7/8) supply, orange (1/2) ground and RS-485
reference, green spare. In the basement: an isolated USB-to-RS-485
adapter on the Pi and a DC supply at the sensor's voltage.

Software: a redhouse service polls the sensor about once a second and
writes each reading to InfluxDB. The smoke rule decides "smoke" with a
debounce, for example blocked for 20 s or more. It only logs at first;
it switches the AC only after the thresholds are tuned on real data,
with rain, snow, fog, birds and winter steam plumes in mind.

Needed from the datasheet: the supply voltage, the Modbus settings
(baud rate, address, parity), the register map and unit, the reading
when there is no target, the sample rate and the laser class.

## 7. Interfaces

1. Command line: `redhouse-pump fire`, `redhouse-pump ac on|off|status`.
2. A small HTTP API on the Pi, on the home network only, with a token:
   `POST /fire`, `GET /status`. It calls the same code as the command
   line, so no button can bypass a rule.
3. Grafana: a "Light the oven fire" button and a status panel on the
   redhouse-production dashboard. A button needs a button panel plugin
   or an HTML panel. The browser calls the Pi, so the API must allow the
   dashboard's origin, and the token must not be visible to viewers.
4. Later, an e-ink display that reads `GET /status` and calls the same
   endpoints.

## 8. Interim protection

Until R1 is built, redhouse does not switch the AC at all: not in a
program EVU block, not by `redhouse-pump`, and not at boot. The
ventilation then runs through EVU blocks, which costs only the fan
power, and it can never restart by itself or stay off for days while
nobody is home. The user switches it with the Shelly UI or app.

Before that is deployed, or when the AC is off for any reason: when
you fire the oven, check that no EVU block ends in the next hour. If
one does, or if the fire is lit during an EVU block, turn the AC on by
hand before lighting, so that it runs steadily through the start. Set
"Ylipaine" on before lighting, as on 2026-10-12: the larger smoke
spikes before 2024 came with the AC running steadily.

## 9. Delivery order

Each step has its own tests and a fresh review, and is safe to use alone.

1. This spec.
2. The interim protection (section 8).
3. R2, R3, R6, R7, R9 and the state file, with `redhouse-pump ac`.
4. R1, the fire window, with `redhouse-pump fire`.
5. R11, the overpressure relay, once it is wired.
6. The HTTP API.
7. The Grafana button and status panel.
8. The smoke reader and logging, then the smoke rule (R4).
9. The e-ink display.

## 10. Open questions

- Smoke: the hold length, and whether a new alert extends it. To be
  settled by experiment once the sensor logs (R4).
- Past incidents: the dates are not known. The "Savupiippu" chimney
  sensor shows 188 firings from 2022-11 to 2026-03. The AC switching can
  be rebuilt for 107 of them, from 2024-01, when program EVU blocks
  began. In 4 of them an EVU block end restarted the AC within about 30
  minutes of the fire start, each time after a fire lit during the
  block: 2024-12-05 09:28, 2025-01-20 20:59, 2025-03-01 15:33 and
  2026-01-28 18:49. wibatemp's EVU pulses never switched the AC. This
  fits "a couple of times", but no record proves that these were the
  incidents. The analysis is in C:/tmp/oven-analysis-20261012.
- Correction: the sensor lags the lighting. On 2026-10-12 the fire was
  lit at about 17:45, the first rise came at about 18:25-18:30, and the
  steep rise after 18:45. With a lag of about 40 minutes, the matches
  above restarted the AC 40-75 minutes after lighting, and the restart
  on 2026-02-02 at 11:35, 35 minutes before that rise, falls about 5-10
  minutes after lighting. The matching is redone with a window based
  on the lighting time, with the IQAir indoor PM2.5 and CO2 as evidence.
- Result of the redo, with a lag of 33 +-15 minutes and the IQAir
  indoor data (10 s PM2.5 and CO2, from 2023-09):
  - Two restart backdrafts are confirmed: 2025-01-20 (lit about 20:26
    in an EVU block, AC on at 21:00, PM2.5 peak 33 ug/m3) and
    2026-01-28 (lit about 18:16 in an EVU block, AC on at 19:00, peak
    95 ug/m3). PM2.5 jumped 2-4 minutes after the restart, and CO2
    confirms that the AC came on. Chance gives about 0.2 such cases.
  - Larger smoke spikes happened with no restart: 2024-01-05 18:01
    (325 ug/m3), 2024-01-26 09:08 (265, at lighting, then an airing),
    2023-12-05, 2024-12-22 and 2024-01-19 (83-147). Outdoor air was
    clean at all of them. Their cause is not known: an unlogged manual
    switch, the oven door, a cold chimney, or the steady negative
    pressure of the running ventilation.
  - A normal firing with the AC running: on 2026-10-12 PM2.5 rose from 1 to
    11 ug/m3, from about 25 to 60 minutes after lighting.
  - So the fire window (R1) prevents the confirmed cases, but not the
    larger ones. Indoor PM2.5 must be watched in any case (section 6).
- Because of the lag, the Savupiippu sensor cannot start a fire window.
  The manual mark (R1) is the only trigger. The sensor can only show
  afterwards that a firing took place without a marked window.

## 11. Low priority

- The Shelly power-on setting decides the AC state after a power cut.
  The grid has not failed in six years, and the Pi has a UPS.
- If the ventilation unit can start the supply fan first, or both fans
  together, that removes the hazard at its root.
