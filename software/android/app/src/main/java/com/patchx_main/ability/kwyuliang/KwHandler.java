package com.patchx_main.ability.kwyuliang;

import android.content.Context;
import android.util.Log;

import com.music.kwylsdk.KwHelper;

import java.util.List;

import cn.kuwo.autosdk.api.AudioFocusStatus;
import cn.kuwo.autosdk.api.KWAPI;
import cn.kuwo.autosdk.api.OnAudioFocusChangeListener;
import cn.kuwo.autosdk.api.OnConnectedListener;
import cn.kuwo.autosdk.api.OnEnterListener;
import cn.kuwo.autosdk.api.OnExitListener;
import cn.kuwo.autosdk.api.OnPlayClientMusicsListener;
import cn.kuwo.autosdk.api.OnPlayEndListener;
import cn.kuwo.autosdk.api.OnPlayerModeListener;
import cn.kuwo.autosdk.api.OnPlayerStatusListener;
import cn.kuwo.autosdk.api.OnSearchListener;
import cn.kuwo.autosdk.api.PlayEndType;
import cn.kuwo.autosdk.api.PlayMode;
import cn.kuwo.autosdk.api.PlayState;
import cn.kuwo.autosdk.api.PlayerStatus;
import cn.kuwo.autosdk.api.SearchStatus;
import cn.kuwo.base.bean.Music;


public class KwHandler {
    private static final String TAG = "KwHandler";
    static KWAPI mKwapi;

    public static boolean initKw(Context mContext) {
        if (null == mKwapi) {
            mKwapi = KwHelper.getInstance().init(mContext);
            initKwApiListener();
            mKwapi.startAPP(false);
            return true;
        }
        return false;
    }

    public static void destory() {
        //断开与酷我音乐的连接，取消所有注册的有关于酷我的广播
        if (mKwapi != null) {
            try {
                mKwapi.unBindKuWoApp();
                mKwapi.exitAPP();
            } catch (Exception e) {
                e.printStackTrace();
            }
        }
    }

    public static void searchPlayInApp(KwInfo mBinding) {
        if (mBinding.theme != null) {
            //在线搜索主题
            mKwapi.searchOnlineMusicByTheme(mBinding.theme
                    , (status, first, musics, overTime) -> {
                        //这里将List转化为List<Music>
                        if (status == SearchStatus.SUCCESS) {
                            showMusicList(musics);
                        } else {
                            //搜索失败，自己进行相关提示处理
                            Log.d(TAG, "搜索主题失败");
                        }
                    });


        } else {
            //根据歌曲名、歌手名、专辑名搜索
            //进入客户端搜索(本地，在线)歌曲并播放
            // mKwapi.playClientMusics(mBinding.song, mBinding.artist, mBinding.otherKey);
            //不启动客户端搜索(选中默认第1首的歌曲后进入酷我进行播放)
            mKwapi.searchOnlineMusic(mBinding.artist
                    , mBinding.song
                    , mBinding.otherKey
                    , new OnSearchListener() {
                        @Override
                        public void searchFinshed(SearchStatus status, boolean first, List<Music> musics, boolean overTime) {
                            //这里将List转化为List<Music>
                            if (status == SearchStatus.SUCCESS) {
                                showMusicList(musics);
                            } else {
                                //搜索失败，自己进行相关提示处理
                                Log.d(TAG, "搜索关键字失败");
                            }
                        }
                    });
        }
    }

    /**
     * 这只是一个简单的列表展示，核心是展示mKwapi.playMusic接口的使用
     *
     * @param musics
     */
    private static void showMusicList(final List<Music> musics) {
        if (musics == null || musics.size() == 0) {
            Log.d(TAG, "列表为空！");
            return;
        }
        Log.e(TAG, "showMusicList: " + musics.size());
        //全部播放[不进入酷我播放]
        //mKwapi.playMusic(musics, 0, false, false, false);
        //全部播放[进入酷我，3s后退出]
        mKwapi.playMusic(musics, 0, true, true, false);

    }


    public static void playOneMusic() {
        if (mKwapi != null) {
            mKwapi.randomPlayMusic();
        } else {
            Log.d(TAG, "mKwapi is Null in playOneMusic");
        }
    }

    public static void kwPlayCtrl(KwPlayAction action) {
        if (mKwapi != null) {
            switch (action) {
                case STATE_PLAY://播放
                    mKwapi.setPlayState(PlayState.STATE_PLAY);
                    break;
                case STATE_PAUSE://暂停
                    mKwapi.setPlayState(PlayState.STATE_PAUSE);
                    break;
                case STATE_PRE:
                    //上一曲
                    mKwapi.setPlayState(PlayState.STATE_PRE);
                    break;
                case STATE_NEXT:
                    //下一曲
                    mKwapi.setPlayState(PlayState.STATE_NEXT);
                    break;
                case MODE_ALL_ORDER:
                    //顺序播放
                    mKwapi.setPlayMode(PlayMode.MODE_ALL_ORDER);
                    break;
                case MODE_ALL_CIRCLE:
                    //列表循环播放
                    mKwapi.setPlayMode(PlayMode.MODE_ALL_CIRCLE);
                    break;
                case MODE_SINGLE_CIRCLE:
                    //单曲循环
                    mKwapi.setPlayMode(PlayMode.MODE_SINGLE_CIRCLE);
                    break;
                case MODE_ALL_RANDOM:
                    //随机播放
                    mKwapi.setPlayMode(PlayMode.MODE_ALL_RANDOM);
                    break;
            }
        } else {
            Log.d(TAG, "mKwapi is Null in kwPlayCtrl");
        }
    }

    public static void initKwApiListener() {
        mKwapi.registerConnectedListener(new OnConnectedListener() {
            @Override
            public void onConnectChangeListener(boolean isConnected) {
                if (isConnected) {
                    Log.d(TAG, "initKwApiListener 音乐盒后台服务已开启");
                } else {
                    Log.d(TAG, "initKwApiListener 音乐盒后台服务已断开");
                }
            }
        });
        mKwapi.registerEnterListener(new OnEnterListener() {

            @Override
            public void onEnter() {
                mKwapi.bindAutoSdkService();
                Log.d(TAG, "appStart 应用启动收到了，开始绑定后台服务！");


                //Toast.makeText(MainActivity.this, "应用启动收到了", Toast.LENGTH_SHORT).show();
            }
        });

        //注册歌曲播放记录的监听，这个接口用于计算播歌数，一般不需要设置！！！
        mKwapi.registerPlayEndListener(new OnPlayEndListener() {
            /**
             * 用于计算歌曲的播放数，回调一次表示播放完一首歌曲
             * @param arg0，
             * END_COMPLETE, //正常播放完
             * END_USER, //用户切歌
             * END_ERROR //异常结束
             */
            @Override
            public void onPlayEnd(PlayEndType arg0) {
                // do something by yourself
                switch (arg0) {
                    case END_COMPLETE:
                        //Toast.makeText(MainActivity.this, "SDK提示:播放完成", Toast.LENGTH_SHORT).show();
                        break;
                    case END_USER:
                        //Toast.makeText(MainActivity.this, "SDK提示:切换歌曲", Toast.LENGTH_SHORT).show();
                        break;
                    case END_ERROR:
                        //Toast.makeText(MainActivity.this, "SDK提示:播歌异常", Toast.LENGTH_SHORT).show();
                        break;
                    default:
                        break;
                }
            }
        });

        //注册获取播放状态的监听
        mKwapi.registerPlayerStatusListener(new OnPlayerStatusListener() {

            @Override
            public void onPlayerStatus(PlayerStatus arg0, Music music) {
                // do something by yourself
                String des = "";
                if (music != null) {
                    des += " 歌名：" + music.name;
                    des += " 歌手：" + music.artist;
                    des += " 专辑：" + music.album;
                }
                Log.i(" 广播", arg0.toString() + des);
            }
        });
        mKwapi.registerPlayerModeListener(new OnPlayerModeListener() {

            @Override
            public void onPlayerMode(int Mode) {
                switch (Mode) {
                    case OnPlayerModeListener.MODE_ALL_CIRCLE:
                        Log.i(" 广播", "全部循环");
                        break;
                    case OnPlayerModeListener.MODE_ALL_ORDER:
                        Log.i(" 广播", "全部播放");
                        break;
                    case OnPlayerModeListener.MODE_ALL_RANDOM:
                        Log.i(" 广播", "随机播放");
                        break;
                    case OnPlayerModeListener.MODE_SINGLE_CIRCLE:
                        Log.i(" 广播", "单曲循环");
                        break;
                }
            }
        });

        mKwapi.registerPlayClientMusicsListener(new OnPlayClientMusicsListener() {

            @Override
            public void onPlayClientMusics(SearchStatus var1, List var2) {
                if (var1 == SearchStatus.SUCCESS) {
                    Log.i(TAG, "搜索成功！");
                } else {

                }
            }

        });

        mKwapi.registerExitListener(new OnExitListener() {

            @Override
            public void onExit() {
                Log.i(TAG, "onExit 收到了");
                mKwapi.unbindAutoSdkService();

            }
        });

        mKwapi.registerAudioFocusChangeListener(new OnAudioFocusChangeListener() {
            @Override
            public void onAudioFocusChange(AudioFocusStatus audioFocusStatus, boolean isMV) {
                StringBuilder msg = new StringBuilder("播放");
                if (isMV) {
                    msg.append("MV");
                } else {
                    msg.append("歌曲");
                }
                if (AudioFocusStatus.AUDIOFOCUS_GAIN.equals(audioFocusStatus)) {
                    msg.append("获取了音频焦点");

                } else if (AudioFocusStatus.AUDIOFOCUS_LOSS.equals(audioFocusStatus)) {
                    msg.append("失去了音频焦点");

                }
            }
        });
    }


}
