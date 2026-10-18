---------------------------------------------------------------------------------------------------
-- Copyright (c) 2026 by Julian Schneider
-- Authors: Julian Schneider
---------------------------------------------------------------------------------------------------

---------------------------------------------------------------------------------------------------
-- Description
---------------------------------------------------------------------------------------------------
-- Text helpers of the far end of the system testbenches (rs_far_end): the words, hexadecimal
-- numbers and packets of the stimulus file (written by sw/refsys/sysvectors.py) and the hexadecimal
-- packets of the records.
--
-- Documentation: sim/README.md

---------------------------------------------------------------------------------------------------
-- Libraries
---------------------------------------------------------------------------------------------------
library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;

library std;
    use std.textio.all;

---------------------------------------------------------------------------------------------------
-- Package Header
---------------------------------------------------------------------------------------------------
package rs_tb_pkg is

    subtype Byte_t is std_logic_vector(7 downto 0);
    type ByteArray_t is array (natural range <>) of Byte_t;

    -- Next word of a line (words are separated by single spaces); Len = 0 at the end of the line
    procedure readWord (
        L   : inout line;
        W   : out   string;
        Len : out   natural);

    -- Whether the first Len characters of W are the word Ref
    function isWord (
        W   : string;
        Len : natural;
        Ref : string) return boolean;

    -- Hexadecimal number of up to 8 digits
    procedure readHex32 (
        L : inout line;
        V : out   std_logic_vector(31 downto 0));

    -- Bytes of a packet: a space, then 2 hexadecimal digits per byte, the first byte first
    procedure readBytes (
        L     : inout line;
        Count : in    natural;
        B     : inout ByteArray_t);

    -- Hexadecimal digits of the first Count bytes of B, the first byte first
    function toHex (
        B     : ByteArray_t;
        Count : natural) return string;

end package;

---------------------------------------------------------------------------------------------------
-- Package Body
---------------------------------------------------------------------------------------------------
package body rs_tb_pkg is

    constant Digits_c : string(1 to 16) := "0123456789abcdef";

    function hexValue (
        C : character) return natural is
    begin

        case C is
            when '0' to '9' =>
                return character'pos(C) - character'pos('0');
            when 'a' to 'f' =>
                return character'pos(C) - character'pos('a') + 10;
            when 'A' to 'F' =>
                return character'pos(C) - character'pos('A') + 10;
            when others =>
                report "rs_tb_pkg: '" & C & "' is not a hexadecimal digit"
                    severity failure;
                return 0;
        end case;

    end function;

    procedure readWord (
        L   : inout line;
        W   : out   string;
        Len : out   natural) is
        variable C_v   : character;
        variable Len_v : natural := 0;
    begin
        W := (W'range => ' ');

        while L /= null and L'length > 0 loop
            read(L, C_v);
            exit when C_v = ' ' and Len_v > 0;

            if C_v /= ' ' then
                Len_v                := Len_v + 1;
                W(W'low + Len_v - 1) := C_v;
            end if;
        end loop;

        Len := Len_v;
    end procedure;

    function isWord (
        W   : string;
        Len : natural;
        Ref : string) return boolean is
    begin
        return Len = Ref'length and W(W'low to W'low + Len - 1) = Ref;
    end function;

    procedure readHex32 (
        L : inout line;
        V : out   std_logic_vector(31 downto 0)) is
        variable W_v   : string(1 to 8);
        variable Len_v : natural;
        variable V_v   : unsigned(31 downto 0) := (others => '0');
    begin
        readWord(L, W_v, Len_v);
        assert Len_v > 0
            report "rs_tb_pkg: hexadecimal number missing"
            severity failure;

        for I in 1 to Len_v loop
            V_v := V_v(27 downto 0) & to_unsigned(hexValue(W_v(I)), 4);
        end loop;

        V := std_logic_vector(V_v);
    end procedure;

    procedure readBytes (
        L     : inout line;
        Count : in    natural;
        B     : inout ByteArray_t) is
        variable C_v : character;
        variable S_v : string(1 to 2 * Count);
    begin
        if Count = 0 then
            return;
        end if;
        -- the digits in one read: reading a long line character by character copies it every time
        read(L, C_v);
        read(L, S_v);

        for I in 0 to Count - 1 loop
            B(B'low + I) := std_logic_vector(to_unsigned(16 * hexValue(S_v(2 * I + 1)) + hexValue(S_v(2 * I + 2)), 8));
        end loop;

    end procedure;

    function toHex (
        B     : ByteArray_t;
        Count : natural) return string is
        variable S_v : string(1 to 2 * Count);
    begin

        for I in 0 to Count - 1 loop
            S_v(2 * I + 1) := Digits_c(to_integer(unsigned(B(B'low + I)(7 downto 4))) + 1);
            S_v(2 * I + 2) := Digits_c(to_integer(unsigned(B(B'low + I)(3 downto 0))) + 1);
        end loop;

        return S_v;
    end function;

end package body;
