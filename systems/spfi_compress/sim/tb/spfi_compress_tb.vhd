---------------------------------------------------------------------------------------------------
-- Copyright (c) 2026 by Julian Schneider
-- Authors: Julian Schneider
---------------------------------------------------------------------------------------------------

---------------------------------------------------------------------------------------------------
-- Description
---------------------------------------------------------------------------------------------------
-- System testbench of spfi_compress: the generated system and the far end of the system
-- testbenches (refsys_tb.rs_far_end), a SpaceFibre node that stands for the test equipment of the
-- hardware test, joined by their serial lines (lane i to lane i, both directions) and fed by one
-- reference clock and one system clock. The far end plays stimulus.txt (written by sim/vectors.py
-- from the hardware test) and records what it receives; sim/vectors.py checks the records with the
-- procedure of the hardware test.
--
-- Documentation: systems/spfi_compress/sim/README.md

---------------------------------------------------------------------------------------------------
-- Libraries
---------------------------------------------------------------------------------------------------
library ieee;
    use ieee.std_logic_1164.all;

library refsys_tb;

library spfi_compress;

---------------------------------------------------------------------------------------------------
-- Entity
---------------------------------------------------------------------------------------------------
entity spfi_compress_tb is
    generic (
        -- Far end: the generics of the SpaceFibre port of the system (ofb0 of the Composer file)
        NumLanes_g  : positive := 4;
        NumVc_g     : positive := 2;
        VcInDepth_g : positive := 1024;
        -- Reference clock of the transceivers in kHz: 156250 (156.25 MHz); the AMD transceiver model (xsim)
        -- also takes 100000 (100 MHz), the behavioural adapter model needs 156250 (its lane clock is the
        -- reference clock)
        RefClkKhz_g : positive := 156250;
        -- System clock in kHz: 200000 (200 MHz); the hardware builds run at 150000 (150 MHz)
        SysClkKhz_g : positive := 200000
    );
end entity;

---------------------------------------------------------------------------------------------------
-- Architecture
---------------------------------------------------------------------------------------------------
architecture sim of spfi_compress_tb is

    -- Clocks: reference clock of the transceivers (RefClkKhz_g, default 156.25 MHz), system clock
    -- (SysClkKhz_g, default 200 MHz)
    constant RefPeriod_c : time := 1 ps * (1.0e9 / real(RefClkKhz_g));
    constant SysPeriod_c : time := 1 ps * (1.0e9 / real(SysClkKhz_g));

    -- Clocks and reset
    signal RefClk : std_logic := '0';
    signal ClkSys : std_logic := '0';
    signal Rst    : std_logic := '1';

    -- Serial lines: system to far end and back
    signal SysTxP : std_logic_vector(NumLanes_g - 1 downto 0);
    signal SysTxN : std_logic_vector(NumLanes_g - 1 downto 0);
    signal FarTxP : std_logic_vector(NumLanes_g - 1 downto 0);
    signal FarTxN : std_logic_vector(NumLanes_g - 1 downto 0);

begin

    -----------------------------------------------------------------------------------------------
    -- Clocks and reset
    -----------------------------------------------------------------------------------------------
    RefClk <= not RefClk after RefPeriod_c / 2;
    ClkSys <= not ClkSys after SysPeriod_c / 2;
    Rst    <= '1', '0' after 1 us;

    -----------------------------------------------------------------------------------------------
    -- System
    -----------------------------------------------------------------------------------------------
    -- The port names of the generated system follow the Composer: <instance>_<port>
    -- vsg_off port_map_002

    i_dut : entity spfi_compress.spfi_compress
        port map (
            Rst        => Rst,
            Clk_sys    => ClkSys,
            pa0_Gt_TxP => SysTxP,
            pa0_Gt_TxN => SysTxN,
            pa0_Gt_RxP => FarTxP,
            pa0_Gt_RxN => FarTxN,
            pa0_RefClk => RefClk
        );

    -- vsg_on port_map_002

    -----------------------------------------------------------------------------------------------
    -- Far end
    -----------------------------------------------------------------------------------------------
    i_far : entity refsys_tb.rs_far_end
        generic map (
            NumLanes_g  => NumLanes_g,
            NumVc_g     => NumVc_g,
            VcInDepth_g => VcInDepth_g
        )
        port map (
            RefClk => RefClk,
            ClkSys => ClkSys,
            Rst    => Rst,
            Gt_TxP => FarTxP,
            Gt_TxN => FarTxN,
            Gt_RxP => SysTxP,
            Gt_RxN => SysTxN
        );

end architecture;
