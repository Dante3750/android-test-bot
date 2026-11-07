package com.example.samplebot;

import android.app.Activity;
import android.os.Bundle;
import android.view.Gravity;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Tiny demo app: a counter, a reset button, and a "say hello" box. */
public class MainActivity extends Activity {
    private int count = 0;
    private TextView countText;
    private TextView helloText;

    public static int add(int a, int b) { return a + b; }

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        if (saved != null) count = saved.getInt("count");

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER_HORIZONTAL);
        root.setPadding(48, 96, 48, 48);

        countText = new TextView(this);
        countText.setTextSize(32);
        root.addView(countText);

        Button plus = new Button(this);
        plus.setText("Add one");
        plus.setOnClickListener(v -> { count++; show(); });
        root.addView(plus);

        Button reset = new Button(this);
        reset.setText("Reset");
        reset.setOnClickListener(v -> { count = 0; show(); });
        root.addView(reset);

        EditText name = new EditText(this);
        name.setHint("Your name");
        name.setContentDescription("name box");
        root.addView(name);

        Button hello = new Button(this);
        hello.setText("Say hello");
        hello.setOnClickListener(v -> helloText.setText("Hello, " + name.getText().toString().trim() + "!"));
        root.addView(hello);

        helloText = new TextView(this);
        helloText.setTextSize(20);
        root.addView(helloText);

        setContentView(root);
        show();
    }

    private void show() { countText.setText("Count: " + count); }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        out.putInt("count", count);
    }
}
