package com.patchx_main.ability.aiui.bean;

public class SmartHomeBean {

    private String attr;
    private String attrType;
    private String attrValue;
    private Location location;

    public void setAttr(String attr) {
        this.attr = attr;
    }

    public String getAttr() {
        return attr;
    }

    public void setAttrType(String attrType) {
        this.attrType = attrType;
    }

    public String getAttrType() {
        return attrType;
    }

    public void setAttrValue(String attrValue) {
        this.attrValue = attrValue;
    }

    public String getAttrValue() {
        return attrValue;
    }

    public void setLocation(Location location) {
        this.location = location;
    }

    public Location getLocation() {
        return location;
    }



    public static class Location {

        private String room;
        private String type;

        public void setRoom(String room) {
            this.room = room;
        }

        public String getRoom() {
            return room;
        }

        public void setType(String type) {
            this.type = type;
        }

        public String getType() {
            return type;
        }

        @Override
        public String toString() {
            return "Location{" +
                    "room='" + room + '\'' +
                    ", type='" + type + '\'' +
                    '}';
        }
    }

    @Override
    public String toString() {
        return "SmartHomeBean{" +
                "attr='" + attr + '\'' +
                ", attrType='" + attrType + '\'' +
                ", attrValue='" + attrValue + '\'' +
                ", location=" + location +
                '}';
    }
}
