package com.patchx_main.ability.tts;

import android.content.res.AssetFileDescriptor;
import android.media.AudioManager;
import android.media.MediaPlayer;
import android.util.Log;

import java.io.IOException;

public class MusicPlayEngine extends BasePlayEngine {
    private static final String TAG = "MusicPlayEngine";
    private MediaPlayer mediaPlayer;

    @Override
    public void init() {
        mediaPlayer = new MediaPlayer();
    }

    private void reset() {
        mTtsCallBack = null;
        if (mediaPlayer != null) {
            mediaPlayer.reset();
        }
    }

    @Override
    protected void play(String path, IPlayCallBack iTtsCallBack) {
        try {
            Log.d(TAG, "play: " + path);
            reset();
            mTtsCallBack = iTtsCallBack;
            mediaPlayer.setDataSource(path);
            startPlay();
        } catch (IOException e) {
            e.printStackTrace();
            reset();
            callBack(playError, e.getMessage());
        }
    }

    private void startPlay() {
        mediaPlayer.setAudioStreamType(AudioManager.STREAM_MUSIC);
        mediaPlayer.setOnPreparedListener(mp -> {
            mediaPlayer.start();
        });
        mediaPlayer.setOnCompletionListener(mp -> {
            callBack(playEnd, "");
        });
        mediaPlayer.setOnErrorListener((mp, what, extra) -> {
            reset();
            callBack(playError, "");
            return true;
        });
        mediaPlayer.prepareAsync();
    }

    @Override
    protected void play(AssetFileDescriptor assetFileDescriptor, IPlayCallBack iTtsCallBack) {
        try {
            reset();
            mTtsCallBack = iTtsCallBack;
            mediaPlayer.setDataSource(assetFileDescriptor.getFileDescriptor(), assetFileDescriptor.getStartOffset(), assetFileDescriptor.getLength());
            startPlay();
        } catch (IOException e) {
            e.printStackTrace();
            reset();
            callBack(playError, e.getMessage());
        }
    }

    @Override
    public void stop() {
        if (mediaPlayer != null) {
            if (mediaPlayer.isPlaying()) {
                mediaPlayer.stop();
            } else {
                mediaPlayer.reset();
            }
        }
    }

    @Override
    public void release() {
        if (mediaPlayer != null) {
            mediaPlayer.release();
            mediaPlayer = null;
        }
    }
}
