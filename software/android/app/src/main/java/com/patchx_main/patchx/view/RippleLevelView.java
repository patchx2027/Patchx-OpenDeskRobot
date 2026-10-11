package com.patchx_main.patchx.view;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.util.AttributeSet;
import android.view.View;

public class RippleLevelView extends View {

    private float level = 0f; // 0~1
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF rect = new RectF();

    public RippleLevelView(Context context) {
        super(context);
        init();
    }

    public RippleLevelView(Context context, AttributeSet attrs) {
        super(context, attrs);
        init();
    }

    private void init() {
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(4f);
        paint.setColor(Color.GREEN);
    }

    public void setLevel(float level) {
        this.level = Math.max(0f, Math.min(1f, level));
        invalidate(); // 刷新界面
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float radius = (Math.min(getWidth(), getHeight()) / 2f) * level;
        rect.set(getWidth() / 2f - radius, getHeight() / 2f - radius,
                getWidth() / 2f + radius, getHeight() / 2f + radius);
        canvas.drawOval(rect, paint);
    }
}
