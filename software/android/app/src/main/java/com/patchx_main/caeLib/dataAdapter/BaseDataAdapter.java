package com.patchx_main.caeLib.dataAdapter;

public abstract class BaseDataAdapter {
    protected static byte[] outData;

    public abstract byte[] adapter(byte[] source);
}
