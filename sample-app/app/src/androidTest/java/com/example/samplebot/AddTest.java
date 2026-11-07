package com.example.samplebot;

import static org.junit.Assert.assertEquals;

import java.util.ArrayList;
import java.util.Collection;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.Parameterized;

/** 400 quick checks, so you can see the bot split a big test suite across emulators. */
@RunWith(Parameterized.class)
public class AddTest {
    @Parameterized.Parameters(name = "add_{0}_{1}")
    public static Collection<Object[]> data() {
        Collection<Object[]> cases = new ArrayList<>();
        for (int a = 0; a < 20; a++)
            for (int b = 0; b < 20; b++)
                cases.add(new Object[]{a, b});
        return cases;
    }

    private final int a, b;
    public AddTest(int a, int b) { this.a = a; this.b = b; }

    @Test public void addsCorrectly() {
        assertEquals(a + b, MainActivity.add(a, b));
    }
}
