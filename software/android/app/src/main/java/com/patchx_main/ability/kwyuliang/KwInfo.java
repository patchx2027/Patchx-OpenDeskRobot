package com.patchx_main.ability.kwyuliang;

public class KwInfo {
    public String song;
    public String artist;
    public String theme = null;
    public String otherKey;

    public void clean() {
        this.song = "";
        this.artist = "";
        this.theme = null;
        this.otherKey = "";
    }

    public String toString() {
        return "kwinfo: \nsong=" + song + "  artist=" + artist + " theme=" + theme + "  otherKey=" + otherKey;
    }
}
