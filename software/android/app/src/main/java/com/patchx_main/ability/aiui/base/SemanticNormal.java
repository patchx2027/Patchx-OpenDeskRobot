package com.patchx_main.ability.aiui.base;

import java.util.List;

public class SemanticNormal {
    private String intent;
    private List<Slots> slots;

    public String getIntent() {
        return intent;
    }

    public void setIntent(String intent) {
        this.intent = intent;
    }

    public List<Slots> getSlots() {
        return slots;
    }

    public void setSlots(List<Slots> slots) {
        this.slots = slots;
    }

    @Override
    public String toString() {
        return "Semantic{" +
                "intent='" + intent + '\'' +
                ", slots=" + slots +
                '}';
    }

    public static class Slots {
        private String name;
        private String normValue;
        private String value;

        public String getName() {
            return name;
        }

        public void setName(String name) {
            this.name = name;
        }

        public String getNormValue() {
            return normValue;
        }

        public void setNormValue(String normValue) {
            this.normValue = normValue;
        }

        public String getValue() {
            return value;
        }

        public void setValue(String value) {
            this.value = value;
        }

        @Override
        public String toString() {
            return "Slots{" +
                    "name='" + name + '\'' +
                    ", normValue='" + normValue + '\'' +
                    ", value='" + value + '\'' +
                    '}';
        }
    }

}
