package com.example.samplebot;

import static androidx.test.espresso.Espresso.closeSoftKeyboard;
import static androidx.test.espresso.Espresso.onView;
import static androidx.test.espresso.action.ViewActions.click;
import static androidx.test.espresso.action.ViewActions.typeText;
import static androidx.test.espresso.assertion.ViewAssertions.matches;
import static androidx.test.espresso.matcher.ViewMatchers.isDisplayed;
import static androidx.test.espresso.matcher.ViewMatchers.withContentDescription;
import static androidx.test.espresso.matcher.ViewMatchers.withText;

import androidx.test.ext.junit.rules.ActivityScenarioRule;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.junit.Rule;
import org.junit.Test;
import org.junit.runner.RunWith;

/** UI tests: real taps and typing on the screen. */
@RunWith(AndroidJUnit4.class)
public class MainActivityTest {
    @Rule
    public ActivityScenarioRule<MainActivity> rule = new ActivityScenarioRule<>(MainActivity.class);

    @Test public void startsAtZero() {
        onView(withText("Count: 0")).check(matches(isDisplayed()));
    }

    @Test public void addOneOnce() {
        onView(withText("Add one")).perform(click());
        onView(withText("Count: 1")).check(matches(isDisplayed()));
    }

    @Test public void addOneFiveTimes() {
        for (int i = 0; i < 5; i++) onView(withText("Add one")).perform(click());
        onView(withText("Count: 5")).check(matches(isDisplayed()));
    }

    @Test public void resetGoesBackToZero() {
        onView(withText("Add one")).perform(click());
        onView(withText("Add one")).perform(click());
        onView(withText("Reset")).perform(click());
        onView(withText("Count: 0")).check(matches(isDisplayed()));
    }

    @Test public void countSurvivesRecreate() {
        onView(withText("Add one")).perform(click());
        rule.getScenario().recreate();
        onView(withText("Count: 1")).check(matches(isDisplayed()));
    }

    @Test public void deliberatelyBroken() {   // temporary: checks the failure video
        onView(withText("Add one")).perform(click());
        onView(withText("Count: 99")).check(matches(isDisplayed()));
    }

    @Test public void saysHello() {
        onView(withContentDescription("name box")).perform(typeText("Himanshu"));
        closeSoftKeyboard();
        onView(withText("Say hello")).perform(click());
        onView(withText("Hello, Himanshu!")).check(matches(isDisplayed()));
    }
}
