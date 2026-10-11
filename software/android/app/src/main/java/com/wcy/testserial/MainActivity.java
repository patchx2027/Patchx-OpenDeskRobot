//package com.wcy.testserial;
//
//import static android.app.smdt.util.ErrorCode.RET_API_OK;
//
//import android.app.smdt.SmdtManagerNew;
//import android.os.Bundle;
//import android.os.RemoteException;
//import android.util.Log;
//
//import androidx.appcompat.app.AppCompatActivity;
//
//import java.io.File;
//
//public class MainActivity extends AppCompatActivity {
//
//    private SerialPortUtil serialPortUtil;
//
//    @Override
//    protected void onCreate(Bundle savedInstanceState) {
//        super.onCreate(savedInstanceState);
//        setContentView(R.layout.activity_main); // 如果你有布局文件
//        sendWithSmdtAction();
//        openSerial();
//    }
//    public void sendWithSmdtAction(){
//        SmdtManagerNew smdtManagerNew = SmdtManagerNew.getInstance(this);
//        String artPath = smdtManagerNew.dev_getUartPath("uart3");
//
//        String uart = "/dev/ttySMT4";
//        int baudrate = 115200;
//        int databits = 8;
//        int stopbits = 1;
//        int parity = 0;
//        int flow_ctrl = 0;
//        int result = smdtManagerNew.dev_openUart(uart, baudrate, databits, stopbits, parity, flow_ctrl);//打开串口
//        if(result == RET_API_OK){
//            smdtManagerNew.dev_sendUart("/dev/ttySMT4","test",true);
//            //打开成功后保持接收串口数据
//            smdtManagerNew.dev_receiveUart(uart, new SmdtManagerNew.DataCallback() {
//                @Override
//                public void onDataReceive(byte[] buffer, int size) throws RemoteException {
//                    if (buffer != null) {
//                        Log.d("","data size :" + size);
//                    }
//                }
//            });
//        }
//
//        Log.d("","");
//    }
//    public void openSerial() {
//       try{
//           serialPortUtil= new SerialPortUtil();
//           int result = serialPortUtil.open("/dev/ttySMT4", 115200);
//           if (result != 0) {
//               Log.e("Upgrade", "串口打开失败！");
//               return;
//           }
//           File binFile = new File("/sdcard/update.bin");
//           FirmwareUpgradeManager manager = new FirmwareUpgradeManager(serialPortUtil);
//
//           manager.sendFirmware(binFile, new FirmwareUpgradeCallback() {
//               @Override
//               public void onProgress(int current, int total) {
//                   Log.i("Upgrade", "进度：" + current + " / " + total);
//               }
//
//               @Override
//               public void onSuccess() {
//                   Log.i("Upgrade", "升级成功！");
//               }
//
//               @Override
//               public void onFailure(String error) {
//                   Log.e("Upgrade", "升级失败: " + error);
//               }
//           });
//
//       } catch (Exception e) {
//           throw new RuntimeException(e);
//       }
//    }
//
//
//    @Override
//    protected void onDestroy() {
//        super.onDestroy();
//        if (serialPortUtil != null) {
//            serialPortUtil.close();
//            Log.i("Serial", "串口已关闭");
//        }
//    }
//}
