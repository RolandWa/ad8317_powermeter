# DESIGN REQUIREMENTS DOCUMENT (DRD)

**Project Title:** Handheld Micro-RF Power Meter (AD8317 + STM32F373RCTx)
**Form Factor / Target Enclosure:** Hammond 1590A Ultra-Compact Shielded Chassis
**Firmware Architecture:** Bare-Metal C / HAL Ecosystem
**Compilation Toolchain:** MSYS64 (arm-none-eabi-gcc) / STM32CubeIDE

---

## Revision History

| Rev | Date | Author | Changes |
| :--- | :--- | :--- | :--- |
| A | 2026-07-19 | Author | Initial release |
| B | 2026-08-10 | Author | Schematic-to-DRD sync after hardware review; added Sec 10 RF connector requirements; added Sec 11 PDN/decoupling strategy; added Sec 12 open items |

### Rev B Summary of Changes

| Item | Rev A (DRD) | Rev B (Current Schematic) |
| :--- | :--- | :--- |
| MCU part marking | STM32F373RCT**6** | STM32F373RCT**x** (same die, KiCad convention) |
| Battery charger (U5) | MCP73831T-2ACI | **BQ24090DGQ** (TI 1A, HVSSOP-10, with NTC thermal control) |
| Analog LDO voltage (U4/U5) | ADP150AUJZ-**2.8**V | ADP150AUJZ-**3.3**V (AD8317 VPOS min 3.0 V satisfied) |
| Digital LDO | AP2112K-3.3 | AP2112K-3.3 (unchanged) |
| EEPROM | Not in DRD | **M24C02-WMN** added (U7, I2C, calibration data) |
| USB-C connector | USB4110-GF-A | **G-Switch GT-USB-7010ASV** (footprint identical) |
| Tactile switches | TACT-66N-F | **Omron B3FS** (6×6 mm SMD, B3FS-100xP footprint) |
| VDDSD supply (STM32 pin 32) | Not specified | Currently routed to `+3V3` (digital) — see Sec 12 open item |

---

## 1. System Block Diagram & Structural Architecture

The architecture relies on a strict physical isolation barrier between the sensitive microwave RF frontend and digital processing blocks, enclosed entirely within a 1.6 mm FR-4 two-layer PCB manufactured by JLCPCB.

```text
+---------------------------------------------------------------------------------------+
|                       HAMMOND 1590A ALUMINUM DIE-CAST ENCLOSURE                        |
|                                                                                        |
|  +---------------------------------------------------------------------------------+  |
|  | RF FRONTEND (Shielded Metal Cavity + Absorber Sheet, PGND Plane)                |  |
|  |                                                                                 |  |
|  | [Ext. Attenuator 30dB] -> [SMA Edge-Mount] -> [AD8317 Det.]                     |  |
|  +----------------------------------------------------+----------------------------+  |
|                                                       |                               |
|                                                       V (Analog VOUT DC)              |
|                                           [RC Anti-Aliasing Filter]                   |
|                                           (R=100 Ohm, C=10 nF C0G/NP0)               |
|                                                       |                               |
|  +----------------------------------------------------+----------------------------+  |
|  | METROLOGY & DIGITAL PROCESSING BLOCK               |                            |  |
|  |                                                    V                            |  |
|  |                                          [Pin 19: SDADC1_IN8P]                 |  |
|  |                                       +-------------------------+               |  |
|  | [OLED Display I2C] <===== I2C =======|                         |               |  |
|  | [Button 1: MENU]   -----> GPIO PA0 ->|      STM32F373RCT6      |               |  |
|  | [Button 2: NEXT]   -----> GPIO PA1 ->|        (LQFP64)         |               |  |
|  |                                       |                         |               |  |
|  | [USB-C Connector] <====== USB =======|                         |               |  |
|  |                                       +------------+------------+               |  |
|  |                                                    ^                            |  |
|  |                                                    | VREFSD+ (Pin 15)           |  |
|  |                                        +-----------+-----------+                |  |
|  |                                        | Precision Reference   |                |  |
|  |                                        | [REF3030] (3.00 V)    |                |  |
|  |                                        +-----------+-----------+                |  |
|  +----------------------------------------------------+----------------------------+  |
|                                                       ^                               |
|  +----------------------------------------------------+----------------------------+  |
|  | POWER MANAGEMENT & METROLOGICAL ISOLATION                                       |  |
|  |                                                                                 |  |
|  |                                        +-----------+-----------+                |  |
|  |                                        | ANALOG SUB-RAIL       |                |  |
|  |                                        | (3.3V Ultra-Clean)    |                |  |
|  |                                        +-----------+-----------+                |  |
|  |                                                    ^                            |  |
|  |                                                    | Drain (Keyed Link)         |  |
|  |                                        [T4: P-MOSFET AO3401]                   |  |
|  |                                                    ^                            |  |
|  |                                                    | Source                     |  |
|  |   [USB-C (5V VBUS)]                                |                            |  |
|  |          |                                         |                            |  |
|  |          +-► [MCP73831] ──► [Battery Pack AKY0638 (1200 mAh)]                  |  |
|  |          |                        |                                             |  |
|  |          V (Gate Drive)           V (Kluczowanie)                               |  |
|  |   +────────────────────────────────────────────────────────────+                |  |
|  |   |             TRANSISTORIZED POWER PATH (DIODELESS)           |                |  |
|  |   |   - T2 P-MOSFET (USB)           - T1 P-MOSFET (Battery)    |                |  |
|  |   |   - T3 N-MOSFET (BSS138 Switch Controller)                 |                |  |
|  |   +──────────────────────────────┬─────────────────────────────+                |  |
|  |                                  |                                              |  |
|  |                                  V                                              |  |
|  |                         +────────────────+                                      |  |
|  |                         | GŁÓWNA [V_SYS] |                                      |  |
|  |                         +---+--------+---+                                      |  |
|  |                             |        |                                          |  |
|  |            +────────────────+        +────────────────+                         |  |
|  |            V                                          V                         |  |
|  |      +───────────+                              +───────────+                   |  |
|  |      |   LDO 1   |                              |   LDO 2   |                   |  |
|  |      | AP2112K-3.3|                             |ADP150-2.8V|                   |  |
|  |      | (Digital)  |                             | (Analog)  |                   |  |
|  |      +─────┬─────+                              +─────┬─────+                   |  |
|  |            |                                          |                         |  |
|  |            V (3.3V_DIGITAL)                           V (3.3V_LDO_ANA)          |  |
|  |      +──────────────+                                 │                         |  |
|  |      |  STM32 VDD   |                                 +──► [T4 Keyed P-MOSFET]  |  |
|  |      | OLED Display |                                                           |  |
|  |      +──────────────+                                                           |  |
|  +---------------------------------------------------------------------------------+  |
+---------------------------------------------------------------------------------------+
```

---

## 2. Power Consumption & Battery Life Calculations

| Subsystem Component | Active Mode Current | Micro-Sleep Mode Current | Deep Sleep Mode Current |
| :--- | :--- | :--- | :--- |
| **STM32F373RCT6 (@72MHz)** | 35.00 mA | 5.00 mA | 25.00 µA |
| **AD8317 Frontend** | 30.00 mA | 30.00 mA | 0.00 mA (Hardware Isolated) |
| **SBC-OLED01 Display** | 20.00 mA | 20.00 mA | 0.00 mA (Software Shut-off) |
| **REF3030 Reference** | 0.05 mA | 0.05 mA | 0.00 mA (Hardware Isolated) |
| **Total System Current** | **85.05 mA** | **55.05 mA** | **25.00 µA (0.025 mA)** |

### Operational Lifespan Analysis (Akyga AKY0638 - 1200 mAh)

* **Worst-Case Continuous Active Mode:**\[\text{Lifespan} = \frac{1200\text{ mAh}}{85.05\text{ mA}} \times 0.85 \text{ (derating factor)} = \mathbf{12.0 \text{ hours}}\]
* **Power-Managed Metric (Micro-Sleep between burst sampling):**\[\text{Duty Cycle} = 10\% \text{ Active}, 90\% \text{ Micro-Sleep} \rightarrow I_{\text{average}} = (85.05 \times 0.1) + (55.05 \times 0.9) = 58.05\text{ mA}\]
  \[\text{Real-World Lifespan} = \frac{1200\text{ mAh}}{58.05\text{ mA}} \times 0.85 = \mathbf{17.5 \text{ hours}}\]
* **Deep Sleep Shelf-Life Mode:**
  \[\text{Lifespan} = \frac{1200\text{ mAh}}{0.025\text{ mA}} = 48,000 \text{ hours} \approx \mathbf{5.4 \text{ years}}\]

---

## 3. Physical Interconnections & Hardware Netlist

### Analog Rail Power Supply Selection

The analog rail (+3V3_A) powers the AD8317 log detector and REF3030 voltage reference. These are
noise-sensitive: AD8317 output ripple directly adds to the SDADC measurement error.

| Parameter | AP2112K-3.3 | **ADP150AUJZ-3.3** | Benefit |
| :--- | :--- | :--- | :--- |
| Output voltage | 3.3 V | **3.3 V** | Matches AD8317 VPOS nominal; simplifies BOM (single voltage) |
| Output noise (10 Hz–100 kHz) | ~50 µVrms | **9 µVrms** | 5× lower noise floor on VPOS |
| PSRR @ 1 kHz | 65 dB | **100 dB** | Better battery switcher rejection |
| Quiescent current | 55 µA | **65 µA** | Comparable |
| Package | SOT-23-5 | **TSOT-5** | Same footprint family |
| Datasheet | — | [ADP150](https://www.analog.com/en/products/adp150.html) | |
| Mouser | AP2112K-3.3TRG1 | **584-ADP150AUJZ-3.3R7** | |

**U4 (analog LDO) is ADP150AUJZ-3.3-R7** (Rev B: changed from 2.8 V to 3.3 V to satisfy AD8317 VPOS minimum 3.0 V with margin).  
U6 (digital LDO, +3V3 rail for STM32 VDD / OLED) remains AP2112K-3.3.

The REF3030 voltage reference (U3) is powered from the ADP150 +3V3_A rail and provides a stable
3.00 V to VREFSD+ of the STM32F373 SDADC1. Datasheet:
[REF3030](https://www.analog.com/en/products/ref3030.html).

### Metrological Signal Isolation Netlist

1. **`[VOUT_RF]`** (AD8317 Output Pin 7) → Connects to R_RC (100 Ohm, 0603, 1%).
2. **`[ADC_IN_CLEAN]`** → Connection point between R_RC, C_RC (10 nF, C0G, 0603), and **Pin 19 (PE8/SDADC1_IN8P)** of the STM32F373.
3. **`[AGND_RF]`** (AD8317 Pins 2, 3, 6, 9) → Tied directly to the RF analog ground cavity plane. Connects directly to **Pin 18 (PE7/SDADC1_IN8N)** for single-ended ground referencing.
4. **`[VREF_3V0]`** (REF3030 Pin 2 Output) → Decoupled with 10 µF Tantalum + 100 nF X7R directly to **Pin 15 (VREFSD+)** of the STM32F373.
5. **`[GPIO_PWR_CTRL]`** (**Pin 2 (PC13)** of STM32) → Drives Gate of T4 (AO3401) through a 10k resistor. A logic `HIGH` isolates the analog sub-rail during system deep sleep.

### UI Interface Netlist

* **`[SW_MENU]`** → Tied to **Pin 14 (PA0)** of STM32. Configured with an internal Pull-up. Active Low.
* **`[SW_NEXT]`** → Tied to **Pin 15 (PA1)** of STM32. Configured with an internal Pull-up. Active Low.
* **`[OLED_SCL]`** → Tied to **Pin 43 (PA15)** of STM32 (I2C1_SCL) with an external 4.7k Pull-up to `[3.3V_DIGITAL]`.
* **`[OLED_SDA]`** → Tied to **Pin 42 (PA14)** of STM32 (I2C1_SDA) with an external 4.7k Pull-up to `[3.3V_DIGITAL]`.

---

## 4. Software Emulation Profiles & USB Descriptors

To interface natively with automated laboratory test suites (LabVIEW, MATLAB VISA, Keysight BenchVue), the STM32 USB engine replaces its default product strings with the following Vendor ID (VID) and Product ID (PID) registers based on a user's menu selection.

### Profile A: Keysight / Agilent Emulation

* **Vendor ID (VID):** `0x2A8D` (Keysight Technologies)
* **Product ID (PID):** `0x7E18` (U2000A USB Power Sensor Engine)
* **USB Class:** USBTMC (USB Test and Measurement Class - Interface `0xFE`, Subclass `0x03`, Protocol `0x01`).
* **String Manufacturer:** `"Keysight Technologies"`
* **String Product:** `"U2000A USB Power Sensor"`

### Profile B: Rohde & Schwarz Emulation

* **Vendor ID (VID):** `0x0AAD` (Rohde & Schwarz)
* **Product ID (PID):** `0x00A1` (NRP-Z21 Power Sensor Matrix)
* **USB Class:** USBTMC
* **String Manufacturer:** `"Rohde&Schwarz"`
* **String Product:** `"NRP-Z21 Power Sensor"`

---

## 5. SCPI Command Parser Requirements

The firmware's string parser matches incoming ASCII commands over the USB Endpoints and returns strict SCPI compliance formatting terminated with standard Line Feed (`\n`).

### Mandatory Laboratory Commands Matrix

| Command | Profile Keysight Response | Profile R&S Response | Firmware Internal Routine |
| :--- | :--- | :--- | :--- |
| **`*IDN?`** | `Keysight Technologies,U2000A,MY20261234,A.01.00\n` | `Rohde&Schwarz,NRP-Z21,100001,1.00\n` | Returns raw identifier string. |
| **`READ?`** / **`FETCh?`** | `-15.42\n` *(String Floating Value)* | `-15.42\n` | Triggers DMA 64-sample snapshot, calculates dBm value + current offset. |
| **`*RST`** | `OK\n` | `OK\n` | Flushes internal RAM lookup modifiers, resets calibration back to 30.0dB. |
| **`CALC:GAIN <val>`** | `OK\n` | `OK\n` | Overwrites default 30.0dB offset variable dynamically from Host PC input. |
| **`SENS:FREQ <val>`** | `OK\n` | `OK\n` | Changes calibration curve index. Updates `SLOPE` and `V_INTERCEPT` lookup matrices. |

---

## 6. Firmware Architectural Specification & Logic Flow

### Metrological ADC Pipeline Execution

1. **Clock Configuration:** HSE External 8.000 MHz quartz → PLL multiplied x9 → CPU `SYSCLK` = 72 MHz. Peripheral clock for SDADC divided down to 6 MHz.
2. **Burst Over-sampling via DMA:** The SDADC1 runs in continuous conversion mode triggered via internal timer.
3. **Array Matrix:** The hardware DMA engine streams exactly 64 iterations of 16-bit raw registers straight into an allocated buffer array in SRAM (`uint16_t adc_buffer`).
4. **Processing Pipeline:** Once the DMA Transfer Complete (`TC`) interrupt fires, the CPU runs a rolling average routine, effectively smoothing the noise floor:
   \[V_{\text{calculated}} = \left( \frac{\sum_{i=0}^{63} \text{adc\_buffer}[i]}{64} \right) \times \frac{3.00\text{V}}{65535}\]
5. **Logarithmic Transfer Function Transformation:**
   \[\text{Power (dBm)} = \frac{V_{\text{calculated}} - V_{\text{Intercept\_Frequency}}}{\text{Slope\_Frequency}} + \text{System\_Offset\_dB}\]

### Dynamic State Flow Chart

```text
                    +-----------------------+
                    |   Power ON / Bootup   |
                    +-----------+-----------+
                                |
                                V
                    +-----------------------+
                    |  Initialize Hardware  |
                    |  SystemClock_Config() |  HSE 8MHz x PLL x9 = 72MHz
                    |  MX_GPIO_Init()       |  PA0/PA1 buttons, PC13
                    |  MX_I2C1_Init()       |  PA14/PA15 -> OLED
                    |  MX_USB_DEVICE_Init() |  USBTMC profile A/B
                    |  MX_SDADC1_Init()     |  6MHz sigma-delta
                    |  MX_DMA_Init()        |  circular 64 samples
                    |  Read Flash Profiles  |  VID/PID, CALC:GAIN
                    +-----------+-----------+
                                |
                                V
                    +-----------------------+
                    |  Start 6MHz SDADC1    |
                    |  Arm Circular DMA     |  -> adc_buffer[64]
                    +-----------+-----------+
                                |
                                V
+──────────────────► MAIN OPERATIONAL LOOP ◄──────────────────────────+
│                               │                                      │
│              [DMA TC IRQ fires every 64 samples]                     │
│                               V                                      │
│               +───────────────────────────+                          │
│               |  adc_task()               |                          │
│               |  rolling avg adc_buffer[] |                          │
│               |  V_calc = avg*3.00/65535  |                          │
│               |  dBm = (V-V_int)/slope    |                          │
│               |       + System_Offset_dB  |                          │
│               +───────────────────────────+                          │
│                               │                                      │
│              [HAL_GetTick() >= last_display + 100ms]                 │
│                               V                                      │
│               +───────────────────────────+                          │
│               |  display_task()           |                          │
│               |  SSD1306 refresh I2C1     |                          │
│               |  show dBm + freq + profile|                          │
│               +───────────────────────────+                          │
│                               │                                      │
│              [USB RX endpoint has pending bytes]                     │
│                               V                                      │
│               +───────────────────────────+                          │
│               |  scpi_task()              |                          │
│               |  strtok_r parse command   |                          │
│               |  *IDN?  READ?  FETCh?     |                          │
│               |  *RST  CALC:GAIN  SENS:   |                          │
│               |  write USB TX endpoint    |                          │
│               +───────────────────────────+                          │
│                               │                                      │
│              [HAL_GetTick() >= last_button + 20ms debounce]          │
│                               V                                      │
│               +───────────────────────────+                          │
│               |  button_task()            |                          │
│               |  SW1 PA0: MENU/profile    |                          │
│               |  SW2 PA1: NEXT/freq idx   |                          │
│               |  reset inactivity timer   |                          │
│               +───────────────────────────+                          │
│                               │                                      │
│                               V                                      │
│               +───────────────────────────+                          │
│               |  sleep_task()             |                          │
│               |  inactivity > 5 min?      |                          │
│               |  AND RF power < -50 dBm?  |                          │
│               +──────────────┬────────────+                          │
│                         YES  │  NO                                   │
│                              │ NO ──────────────────────────────────+│
│                         YES  V
│          +──────────────────────────────+
│          |      ENTER DEEP SLEEP        |
│          |  1. SSD1306 display off      |  I2C command
│          |  2. PC13 = HIGH              |  T4 gate -> LDO2 off
│          |     (AD8317 + REF3030 off)   |
│          |  3. HAL_PWR_EnterSTOPMode()  |  CPU STOP, ~25 uA
│          +─────────────────┬────────────+
│                            │
│                            V
│          [ WAIT: EXTI0 (PA0) or EXTI1 (PA1) interrupt ]
│                            │
│                            V
│          +──────────────────────────────+
│          |          WAKE UP             |
│          |  1. SystemClock_Config()     |  restore PLL 72MHz
│          |  2. PC13 = LOW               |  power analog rail
│          |  3. MX_SDADC1_Init()         |  restart sigma-delta
│          |  4. SSD1306 init + splash    |  re-init display
│          |  5. reset inactivity timer   |
│          +─────────────────┬────────────+
│                            │
└────────────────────────────+
```

### Software Module Architecture

```text
firmware/
├── Core/
│   ├── main.c                 main loop, HAL_GetTick() task gates
│   ├── SystemClock_Config()   HSE 8MHz -> PLL x9 -> 72MHz, SDADC /12 = 6MHz
│   └── stm32f3xx_it.c         DMA_TC_IRQHandler, EXTI0_IRQHandler, EXTI1_IRQHandler
│
├── App/
│   ├── adc_task.c/h           DMA TC flag -> rolling avg -> dBm
│   │                          slope/V_intercept LUT indexed by SENS:FREQ
│   ├── display_task.c/h       SSD1306 I2C1 (PA14/PA15), 100ms period
│   ├── scpi_task.c/h          USBTMC RX -> strtok_r -> TX
│   │                          *IDN? READ? FETCh? *RST CALC:GAIN SENS:FREQ
│   ├── button_task.c/h        20ms debounce, menu state machine
│   │                          SW1 PA0 = profile select, SW2 PA1 = freq index
│   ├── sleep_task.c/h         inactivity timer, PC13 rail ctrl, STOP entry/exit
│   └── profile.c/h            VID/PID/strings in Flash (Keysight / R&S)
│
├── USB/
│   ├── usbd_conf.c            STM32 HAL USB FS callbacks
│   ├── usbd_desc.c            VID/PID/strings swapped at runtime
│   └── usbd_usbtmc.c          USBTMC class 0xFE/0x03/0x01
│
└── Drivers/
    ├── ssd1306/               I2C display driver (128x64 monochrome)
    └── STM32F3xx_HAL/         CubeMX-generated (SDADC, DMA, I2C, USB, GPIO)
```

## 7. Official Bill of Materials (BOM) & Sourcing Matrix

Optimized for **JLCPCB automated assembly** and structural placement inside a Hammond 1590A chassis. Part numbers from **TME, Conrad, and Mouser** are locked below to enforce design verification.

| Reference | Qty | Part Value | Package Type | Supplier | Supplier Part Number | Component Description |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **U1** | 1 | STM32F373RCT6 | LQFP-64 | **TME** | STM32F373RCT6 | Metrological MCU, 256KB Flash, 32KB RAM |
| **U2** | 1 | AD8317 | LFCSP-8 (3x3mm) | **Mouser** | 584-AD8317ACPZ-R7 | Demodulating Logarithmic Amp 10GHz |
| **U3** | 1 | REF3030AIDBZR | SOT-23-3 | **Mouser** | 595-REF3030AIDBZR | Low-Drift Voltage Reference 3.00V, 50ppm |
| **U4** | 1 | AP2112K-3.3TRG1 | SOT-23-5 | **TME** | AP2112K-3.3TRG1 | Fixed LDO 3.3V, 600mA — digital rail (+3V3) |
| **U4** | 1 | ADP150AUJZ-3.3-R7 | TSOT-5 | **Mouser** | 584-ADP150AUJZ-3.3R7 | Ultra-Low-Noise LDO 3.3V, 150mA, 9µVrms — analog rail (+3V3_A) |
| **U5** | 1 | BQ24090DGQ | HVSSOP-10 | **Mouser** | 595-BQ24090DGQ | 1A Single-Input Li-Ion/Li-Pol Charger with NTC temperature sensing |
| **U6** | 1 | AP2112K-3.3 | SOT-23-5 | **TME** | AP2112K-3.3TRG1 | Fixed LDO 3.3V, 600mA — digital rail (+3V3) |
| **U7** | 1 | M24C02-WMN | SOIC-8 | **Mouser** | 511-M24C02-WMN6P | 2Kb I2C EEPROM — calibration curve storage |
| **T1, T2, T4** | 3 | AO3401A | SOT-23 | **JLCPCB** | C13125 (Direct) | Power P-MOSFET, -30V, -4A, RDS(on) < 50mR |
| **T3** | 1 | BSS138 | SOT-23 | **TME** | BSS138-7-F | Signal N-MOSFET Control Switch, Logic-Level |
| **T_PCM** | 1 | FS8205A | TSSOP-8 | **JLCPCB** | C347480 (Direct) | Dual-Channel N-MOSFET Battery Pack Driver |
| **FB1-FB3** | 3 | BLM18HE601SN1D | 0603 | **TME** | BLM18HE601SN1D | High-Freq Ferrite Bead, Z=600R @100MHz |
| **R_PROG** | 1 | 2.7 kΩ | 0603 1% | **JLCPCB** | Standard 2.7k 0603 | Resistor setting charging parameter to 360mA |
| **R_RC** | 1 | 100 Ω | 0603 1% | **JLCPCB** | Standard 100R 0603 | Low-Thermal-Noise Anti-Aliasing Resistor |
| **C_RC** | 1 | 10 nF | 0603 | **TME** | 10nF C0G 0603 | Ultra-Stable C0G/NP0 Ceramic Cap (Zero-drift) |
| **C_REF1-2** | 2 | 10 µF | CASE-A (3216) | **TME** | TAJA106K010RNJ | Tantalum Solid Capacitor, Low-ESR Target |
| **C1-C12** | 12 | 100 nF | 0603 | **JLCPCB** | Standard 100nF X7R | Multilayer Ceramic Decoupling Capacitors |
| **DISP1** | 1 | SBC-OLED01 | Mod. 27x27mm | **Conrad** | 2176922 | 0.96" SSD1306 Graphic Display Matrix, I2C |
| **BAT1** | 1 | Akyga AKY0638 | Li-Po Pack | **TME** | AKY0638 | 3.7V Single-Cell Battery Pack, **1200mAh**, JST |
| **SW1, SW2** | 2 | Omron B3FS | SMD 6×6 mm | **TME** | B3FS-100xP | Tactile switch, 100 gf, SMD, 6×6 mm |
| **J2** | 1 | G-Switch GT-USB-7010ASV | SMT 16P | **Mouser** | USB-C 16P receptacle |
| **J1** | 1 | Amphenol 132289 | Edge-mount SMA | **Mouser** | 132289 | SMA edge-launch, 18 GHz (see Sec 10 for frequency limit notes) |
| **J3** | 1 | 2.54 mm 1×4 socket | Through-hole | — | — | UART/I2C expansion header |

---

## 8. Compilation Toolchain Guide (MSYS64 Environment)

To compile the bare-metal C codebase on an explicit open toolchain via **MSYS64 MinGW**, execute the following commands in your terminal to provision the cross-compiler and link the make environment:

1. **Install arm-none-eabi Toolchain Dependencies:**
   ```bash
   pacman -S ossp
   pacman -S Pack
   pacman -S mingw-w64-x86_64-arm-none-eabi-gcc
   pacman -S mingw-w64-x86_64-arm-none-eabi-newlib
   pacman -S make git
   ```
2. **Project Directory Compilation Trigger:**
   Navigate straight to the codebase containing your generated `Makefile` (from CubeMX project configuration output) and run:
   ```bash
   make -j4
   ```
3. **Flashing Binaries to Target Board via Terminal:**
   ```bash
   st-flash write build/output.bin 0x08000000
   ```

---

## 9. Artificial Intelligence Assistance & Prompts Guideline

This project is explicitly structured to be developed, refactored, and debugged alongside Large Language Models (LLMs) and Code Companions such as **Google Gemini, GitHub Copilot, or Claude**.

When prompting an AI helper for implementation, feed it the rules below to get working code on the first pass.

### Context injection prompt for Copilot / Claude:

> *"You are an expert embedded systems firmware engineer specializing in STM32 bare-metal C and RF metrology applications. We are writing code for an STM32F373RCT6. The hardware platform features a 16-bit Sigma-Delta ADC (SDADC1) measuring an AD8317 logarithmic detector through a 100 Ohm / 10 nF RC filter. An external REF3030 provides a stable 3.00V reference to VREFSD+. The user interface consists of two active-low buttons on PA0 and PA1, and an I2C SSD1306 OLED on PA14/PA15. Pin PC13 controls a P-MOSFET that isolates the analog power rail. Write modular, MISRA-compliant C code utilizing the STM32 HAL library that adheres strictly to these hardware mappings."*

### Key Rules for AI Code Generation:

1. **Never use blocking delays (`HAL_Delay`)** inside the main operational loops or SCPI parsing routines. All timings, including button debounce and OLED frame rates, must rely on non-blocking `HAL_GetTick()` token counters.
2. **Enforce clean register state restoration** during the deep sleep cycle. Remind the AI to include the `SystemClock_Config()` restoration parameters immediately following the execution of `HAL_PWR_EnterSTOPMode()`.

---

## 10. RF Connector Selection Requirements

The SMA (3.5 mm outer conductor) connector family, including all Amphenol 132xx edge-launch variants, is rated to a maximum of **18 GHz** (MIL-C-39012 / IEC 61169-15).  
Beyond 18 GHz the TE₁₁ higher-order mode begins propagating in the outer conductor:

$$f_{cutoff}^{TE_{11}} = \frac{c}{\pi \times d \times \sqrt{\varepsilon_r}} \approx 18\text{ GHz (air dielectric)}$$

This device is designed and calibrated for **1 MHz – 10 GHz** using the AD8317. The SMA connector selection is therefore within specification for the intended operating range.

### Connector frequency budget

| Connector family | Outer conductor Ø | Single-mode cutoff | Suitability |
| :--- | :--- | :--- | :--- |
| **SMA (Amphenol 132289)** | 3.5 mm | ~18 GHz | **Correct for 1 MHz – 10 GHz** |
| 2.92 mm (K) | 2.92 mm | ~40 GHz | Required only if extending to >18 GHz |
| 2.4 mm (V) | 2.40 mm | ~50 GHz | Required only if extending to >40 GHz |

### PCB launch requirements for SMA edge-mount (J1)

1. GCPW trace width on layer 1 (F.Cu) matched to 50 Ω using board stackup εr and layer spacing
2. Coplanar ground gap ≤ 0.15 mm clearance from signal trace
3. Ground via fence along GCPW run: via pitch ≤ λ/8 at 10 GHz in FR-4 (≤ 3.2 mm)
4. Via stub length after signal layer ≤ 0.4 mm; back-drill if via passes through full 4-layer stack
5. No ground plane interruption under the SMA pad footprint within 2× pad diameter

---

## 11. Power Delivery Network (PDN) & Decoupling Strategy

### Philosophy

Decoupling is validated by SIwave Power Integrity (PI) simulation with harmonics analysis on the actual power planes, using physical component placement and via parasitics.  This approach is more accurate than rule-of-thumb multi-stage capacitor stacking because it captures mounted inductance, plane spreading inductance, and real anti-resonance frequencies.

### Chosen decoupling topology

Two-stage flat-impedance approach per supply domain:

| Stage | Value | Package | Self-resonance (typical) | Role |
| :--- | :--- | :--- | :--- | :--- |
| Stage 1 | 100 nF X7R | 0402 | 60–80 MHz | Covers MCU harmonics at 36 / 72 MHz |
| Stage 2 | 10 µF X5R | 0402 | 3–6 MHz | Bulk charge for burst-current events |

No intermediate values (10 nF, 1 nF) are required when SIwave confirms PDN impedance meets target across the relevant band.

### Target impedance

For VDDA and VREF+ domains driving the STM32F373 16-bit SDADC:  
- Noise budget: < 1 mV rms in 1 kHz – 1 MHz band
- SDADC peak current: ~3–5 mA  
- Required PDN impedance: **Z < 0.2–0.3 Ω at 100 kHz – 1 MHz**

This is achievable with 100 nF mounted < 1 mm from VDDA (pin 13) and 10 µF within 3–5 mm, provided mounted inductance L_mount < 0.5 nH.

### SIwave validation checklist

- [ ] PDN impedance at VDDA (pin 13) < 0.3 Ω across 100 kHz – 10 MHz
- [ ] PDN impedance at VREF+ (pin 17) < 0.3 Ω across 100 kHz – 10 MHz
- [ ] VDDSD (pin 32) anti-resonance peaks do not fall within SDADC clock harmonics (6 MHz, 12 MHz, 18 MHz)
- [ ] No plane resonances coincide with MCU PLL frequency (72 MHz) or USB SOF (1 kHz)
- [ ] Analog/digital ground coupling inductance < 1 nH between VSSA (pin 12) and VSS (pin 63)

### Placement rules derived from simulation

1. One 100 nF capacitor shall be placed within 0.5 mm of each VDDA/VSSA pin pair
2. One 10 µF capacitor per supply domain placed within 3 mm of the device
3. REF3030 output decoupling: 1 µF C0G + 100 nF X7R directly on VREF+ net, within 2 mm of pin 17
4. Via placement: decoupling cap vias connect directly to plane, no shared via with other components

---

## 12. Schematic Review Findings & Open Items (2026-08-10)

Review performed against KiCad schematic (Rev A board, 4-layer 1.6 mm FR-4).

### Confirmed correct

| Item | Pin(s) | Net | Status |
| :--- | :--- | :--- | :--- |
| VDDA analog supply | 13 | +3V3_A (ADP150) | ✅ |
| VREF+ and VREFSD+ | 17, 33 | REF3030 3.0 V | ✅ |
| SWD debug: SWDIO/SWCLK | 46, 49 | J5 1.27 mm header | ✅ |
| USB D-/D+ | 44, 45 | PA11/PA12 → USB-C J2 | ✅ |
| I2C1: SCL/SDA | 58, 59 | PB6/PB7 → M24C02 EEPROM | ✅ |
| UART1 TX/RX | 42, 43 | PA9/PA10 → J5 header | ✅ |
| VDET (AD8317 output) | 14 | PA0 / SDADC channel | ✅ |
| BOOT0 | 60 | JP1 solder jumper | ✅ (verify default = GND) |

### Open items requiring PCB layout verification

| # | Item | Risk | Action |
| :--- | :--- | :--- | :--- |
| OI-1 | **VDDSD (pin 32) routed from +3V3 (digital)**  | SDADC noise floor degraded by digital switching | Add ferrite bead (Z≥600Ω@100MHz) between +3V3 and VDDSD, or route from +3V3_A. Validate with SIwave. |
| OI-2 | **VBAT (pin 1) supply source** | RTC / backup domain non-functional if floating | Confirm VBAT hierarchical label resolves to battery net or VDD with diode at power sheet level |
| OI-3 | **BOOT0 JP1 default state** | Unintended boot from UART/USB if BOOT0 floats HIGH | Confirm JP1 pulls BOOT0 to GND by default; bridging only for DFU programming mode |
| OI-4 | **VDDA / VREF+ decoupling proximity** | ADC noise floor | Layout rule: 100 nF within 0.5 mm of pin 13; 1 µF C0G within 1 mm of pin 17 |
| OI-5 | **Input matching network (C6 = 8.2 pF) self-resonance** | 0402 self-resonance ~3–4 GHz; above resonance C6 becomes inductive | Verify EMerge FEM simulation shows acceptable S11 at 6–10 GHz after accounting for inductive behaviour |
3. **Always wrap SCPI text buffers safely**. The USBTMC command buffers must include string tokenization safety (`strtok_r` or `strncmp`) to avoid memory leak panics or buffer overflows during continuous automated sweeps
