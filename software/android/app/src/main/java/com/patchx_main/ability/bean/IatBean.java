package com.patchx_main.ability.bean;

import java.util.List;

/**
 * @author yinxiaowei
 * @date 2019/12/9
 * @description:
 */
public class IatBean {

    /**
     * text : {"sn":2,"ls":true,"bg":0,"ed":0,"pgs":"apd","ws":[{"bg":0,"cw":[{"sc":0,"w":""}]}]}
     */

    private TextBean text;

    public TextBean getText() {
        return text;
    }

    @Override
    public String toString() {
        return "IatBean{" +
                "text=" + text +
                '}';
    }

    public void setText(TextBean text) {
        this.text = text;
    }

    public static class TextBean {
        @Override
        public String toString() {
            return "TextBean{" +
                    "sn=" + sn +
                    ", ls=" + ls +
                    ", bg=" + bg +
                    ", ed=" + ed +
                    ", pgs='" + pgs + '\'' +
                    ", ws=" + ws +
                    '}';
        }

        /**
         * sn : 2
         * ls : true
         * bg : 0
         * ed : 0
         * pgs : apd
         * ws : [{"bg":0,"cw":[{"sc":0,"w":""}]}]
         */

        private int sn;
        private boolean ls;
        private int bg;
        private int ed;
        private String pgs;
        private List<WsBean> ws;

        public int getSn() {
            return sn;
        }

        public void setSn(int sn) {
            this.sn = sn;
        }

        public boolean isLs() {
            return ls;
        }

        public void setLs(boolean ls) {
            this.ls = ls;
        }

        public int getBg() {
            return bg;
        }

        public void setBg(int bg) {
            this.bg = bg;
        }

        public int getEd() {
            return ed;
        }

        public void setEd(int ed) {
            this.ed = ed;
        }

        public String getPgs() {
            return pgs;
        }

        public void setPgs(String pgs) {
            this.pgs = pgs;
        }

        public List<WsBean> getWs() {
            return ws;
        }

        public void setWs(List<WsBean> ws) {
            this.ws = ws;
        }

        public static class WsBean {
            /**
             * bg : 0
             * cw : [{"sc":0,"w":""}]
             */

            private int bg;
            private List<CwBean> cw;

            public int getBg() {
                return bg;
            }

            public void setBg(int bg) {
                this.bg = bg;
            }

            public List<CwBean> getCw() {
                return cw;
            }

            public void setCw(List<CwBean> cw) {
                this.cw = cw;
            }

            @Override
            public String toString() {
                return "WsBean{" +
                        "bg=" + bg +
                        ", cw=" + cw +
                        '}';
            }

            public static class CwBean {
                /**
                 * sc : 0
                 * w :
                 */

                private int sc;
                private String w;

                public int getSc() {
                    return sc;
                }

                public void setSc(int sc) {
                    this.sc = sc;
                }

                public String getW() {
                    return w;
                }

                public void setW(String w) {
                    this.w = w;
                }

                @Override
                public String toString() {
                    return "CwBean{" +
                            "sc=" + sc +
                            ", w='" + w + '\'' +
                            '}';
                }
            }
        }
    }
}
