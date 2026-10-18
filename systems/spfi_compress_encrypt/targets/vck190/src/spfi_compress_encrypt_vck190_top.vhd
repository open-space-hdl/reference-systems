---------------------------------------------------------------------------------------------------
-- Copyright (c) 2026 by Julian Schneider
-- Authors: Julian Schneider
---------------------------------------------------------------------------------------------------

---------------------------------------------------------------------------------------------------
-- Description
---------------------------------------------------------------------------------------------------
-- Board top level of the system spfi_compress_encrypt for the AMD VCK190 evaluation board: the
-- SpaceFibre port of the system on the QSFP1 cage (GTY quad 200, four lanes). The CIPS supplies the
-- system clock (200 MHz) and the fabric reset; two LEDs show the system clock and the released reset.
--
-- Documentation: systems/spfi_compress_encrypt/targets/vck190/README.md

---------------------------------------------------------------------------------------------------
-- Libraries
---------------------------------------------------------------------------------------------------
library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;

library olo;

library unisim;
    use unisim.vcomponents.all;

library spfi_compress_encrypt;

---------------------------------------------------------------------------------------------------
-- Entity
---------------------------------------------------------------------------------------------------
entity spfi_compress_encrypt_vck190_top is
    generic (
        IncludeCips_g : boolean := false -- CIPS block design of tcl/build.tcl (simulation: clock model)
    );
    port (
        -- Transceiver reference clock, 156.25 MHz (MGTREFCLK1 of quad 200)
        GtRefClk_P : in    std_logic;
        GtRefClk_N : in    std_logic;
        -- QSFP1, lanes 0 to 3
        Qsfp_TxP   : out   std_logic_vector(3 downto 0);
        Qsfp_TxN   : out   std_logic_vector(3 downto 0);
        Qsfp_RxP   : in    std_logic_vector(3 downto 0);
        Qsfp_RxN   : in    std_logic_vector(3 downto 0);
        -- LEDs: system clock (toggles every 2**27 cycles), system reset released
        Led        : out   std_logic_vector(1 downto 0)
    );
end entity;

---------------------------------------------------------------------------------------------------
-- Architecture
---------------------------------------------------------------------------------------------------
architecture struct of spfi_compress_encrypt_vck190_top is

    -- Clock and reset: ClkSys (CIPS pl0, 200 MHz), fabric reset of the CIPS
    signal ClkSys    : std_logic             := '0';
    signal PlRstN    : std_logic             := '1';
    signal PorIn     : std_logic;
    signal SysRst    : std_logic;
    signal RefClk    : std_logic;
    signal Heartbeat : unsigned(26 downto 0) := (others => '0');
    signal LedClk    : std_logic             := '0';

    -- Control, interfaces and processing system of the Versal device (block design vck190_cips, created by
    -- tcl/build.tcl): every Versal design needs it, its platform management controller configures the device and
    -- its clock generator drives the system clock (port names of the block design)
    -- vsg_off port_010
    component vck190_cips_wrapper is
        port (
            pl0_clk    : out   std_logic;
            pl0_resetn : out   std_logic
        );
    end component;

-- vsg_on port_010

begin

    -----------------------------------------------------------------------------------------------
    -- Clock and reset
    -----------------------------------------------------------------------------------------------
    g_cips : if IncludeCips_g generate

        -- vsg_off port_map_002
        i_cips : component vck190_cips_wrapper
            port map (
                pl0_clk    => ClkSys,
                pl0_resetn => PlRstN
            );

    -- vsg_on port_map_002

    end generate;

    -- Simulation: model of the system clock of the CIPS
    g_clk_model : if not IncludeCips_g generate
        ClkSys <= not ClkSys after 2500 ps;
    end generate;

    PorIn <= not PlRstN;

    -- Provisional: the reset of the system is released synchronously to its clock, because the transceiver
    -- adapter of the system (ofb_pa_gty) evaluates its reset in the free-running clock domain (Clk_sys) without a
    -- synchroniser of its own. Remove this generator and connect PorIn directly once ofb_pa_gty synchronises its
    -- reset itself.
    i_rst : entity olo.olo_base_reset_gen
        generic map (
            RstPulseCycles_g => 1000,
            SyncTmr_g        => true
        )
        port map (
            Clk    => ClkSys,
            RstIn  => PorIn,
            RstOut => SysRst
        );

    -- Port names of the primitive
    -- vsg_off port_map_002
    i_refclk : component ibufds_gte5
        port map (
            I     => GtRefClk_P,
            IB    => GtRefClk_N,
            CEB   => '0',
            O     => RefClk,
            ODIV2 => open
        );

    -- vsg_on port_map_002

    -----------------------------------------------------------------------------------------------
    -- System
    -----------------------------------------------------------------------------------------------
    -- Port names of the generated system (<clock>_<domain>, <instance>_<interface>_<signal>)
    -- vsg_off port_map_002
    i_system : entity spfi_compress_encrypt.spfi_compress_encrypt
        port map (
            Rst        => SysRst,
            Clk_sys    => ClkSys,
            pa0_Gt_TxP => Qsfp_TxP,
            pa0_Gt_TxN => Qsfp_TxN,
            pa0_Gt_RxP => Qsfp_RxP,
            pa0_Gt_RxN => Qsfp_RxN,
            pa0_RefClk => RefClk
        );

    -- vsg_on port_map_002

    -----------------------------------------------------------------------------------------------
    -- LEDs
    -----------------------------------------------------------------------------------------------
    p_heartbeat : process (ClkSys) is
    begin
        if rising_edge(ClkSys) then
            Heartbeat <= Heartbeat + 1;
            if Heartbeat = (Heartbeat'range => '1') then
                LedClk <= not LedClk;
            end if;
        end if;
    end process;

    Led(0) <= LedClk;
    Led(1) <= not SysRst;

end architecture;
