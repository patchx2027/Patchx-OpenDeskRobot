package com.patchx_main.caeLib.utils;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Locale;

public class ShellUtil {
    private static final String TAG = "ShellUtil";
    private static ArrayList<String> cardNameList = new ArrayList<>();

    static {
        cardNameList.add("Bothlent UAC Dongle");
        cardNameList.add("AC108 USB Audio");
        cardNameList.add("NationalChip UAC Dongle");
    }

    /**
     * 执行命令, 不需要root权限
     */
    public static ArrayList<String> execShellCmd(String cmd) {
        DataOutputStream dos = null;
        DataInputStream dis = null;
        try {
            Process p = Runtime.getRuntime().exec(cmd);
            dos = new DataOutputStream(p.getOutputStream());
            dis = new DataInputStream(p.getInputStream());
            dos.writeBytes(cmd + "\n");
            dos.flush();
            dos.writeBytes("exit\n");
            dos.flush();
            String line;
            ArrayList<String> arrayList = new ArrayList<>();
            while ((line = dis.readLine()) != null) {
                arrayList.add(line);
            }
            return arrayList;
        } catch (Exception e) {
            e.printStackTrace();
        } finally {
            if (dos != null) {
                try {
                    dos.close();
                } catch (IOException e) {
                    e.printStackTrace();
                }
            }
            if (dis != null) {
                try {
                    dis.close();
                } catch (IOException e) {
                    e.printStackTrace();
                }
            }
        }
        return null;
    }


    public static int execRootCmd(String cmd) {
        int result = -1;
        DataOutputStream dos = null;
        try {
            Process p = Runtime.getRuntime().exec("su");
            dos = new DataOutputStream(p.getOutputStream());
            dos.writeBytes(cmd + "\n");
            dos.flush();
            dos.writeBytes("exit\n");
            dos.flush();
            p.waitFor();
            result = p.exitValue();
        } catch (Exception e) {
            e.printStackTrace();
        } finally {
            if (dos != null) {
                try {
                    dos.close();
                } catch (IOException e) {
                    e.printStackTrace();
                }
            }
        }
        return result;
    }

    public static int fetchCard() {
        ArrayList<String> arrayList = execShellCmd("cat /proc/asound/cards");
        if (arrayList != null) {
            for (String line : arrayList) {
                for (String cardName : cardNameList) {
                    if (line.contains(cardName)) {
                        line = line.replace('[', ',');
                        line = line.replace(']', ',');
                        System.out.println("Find USB card parse:" + line);
                        String[] strs = line.split(",");
                        if (strs.length > 0) {
                            String numStr = strs[0].trim();
                            int cardN = Integer.parseInt(numStr);
                            mCardNumber = cardN;
                            System.out.println("USB card Number=" + cardN);
                            return cardN;
                        }
                    }
                }
            }
        }
        return -1;
    }

    public static int mCardNumber = -1;

    public static String fetchCardPermission() {
        int cardN = fetchCard();
        if (cardN >= 0) {
            String cardDevice = String.format(Locale.getDefault(), "pcmC%dD0c", cardN);
            ArrayList<String> arrayList = execShellCmd("ls -l /dev/snd/");
            if (arrayList != null) {
                for (String data : arrayList) {
                    if (data.contains(cardDevice)) {
                        String[] s = data.split(" ");
                        if (s.length > 0) {
                            return s[0];
                        }
                    }
                }
            }
        }
        return null;
    }

    public static boolean haveRoot() {
        try {
            Process p = Runtime.getRuntime().exec("su");
            return true;
        } catch (IOException e) {
            e.printStackTrace();
        }
        return false;
    }
}
