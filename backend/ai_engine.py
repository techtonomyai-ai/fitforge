"""
FitForge AI Engine
Generates personalized workout plans, meal plans, and weekly adjustments
using the OpenAI API.
"""

import os
import json
import logging
from openai import AsyncOpenAI

logger = logging.getLogger("fitforge.ai")

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Valid input values (used for validation upstream)
WORKOUT_GOALS = ["weight_loss", "muscle_gain", "strength", "endurance", "tone"]
EQUIPMENT     = ["none", "dumbbells", "barbell", "gym", "resistance_bands"]
LEVELS        = ["beginner", "intermediate", "advanced"]

# Use gpt-4o for reliable JSON output; change to gpt-4-turbo if preferred
AI_MODEL = "gpt-4o"


async def generate_workout_plan(
    goal: str,
    level: str,
    days_per_week: int,
    equipment: str,
) -> dict:
    """
    Generate a full workout plan.

    Returns a dict with keys:
      plan_name, description, days[]
        → day_name, focus, exercises[]
            → name, sets, reps, rest_seconds, instructions, muscle_groups
    """
    prompt = f"""Create a {days_per_week}-day/week workout plan for {goal}, {level} level, using {equipment}.

Return ONLY valid JSON with this exact structure:
{{
  "plan_name": "...",
  "description": "...",
  "days": [
    {{
      "day_name": "Day 1 – Chest & Triceps",
      "focus": "Push",
      "exercises": [
        {{
          "name": "...",
          "sets": 3,
          "reps": "8-12",
          "rest_seconds": 60,
          "instructions": "...",
          "muscle_groups": ["chest", "triceps"]
        }}
      ]
    }}
  ]
}}

Include 4-6 exercises per day. Make instructions clear and beginner-friendly where appropriate.
Ensure the plan is balanced, progressive, and aligned with the {goal} goal."""

    try:
        response = await client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.7,
        )
        content = response.choices[0].message.content
        return json.loads(content)
    except Exception as e:
        logger.error(f"AI workout generation failed: {e}")
        # Return a sensible fallback
        return {
            "plan_name": f"{goal.replace('_', ' ').title()} Plan",
            "description": f"A {days_per_week}-day {level} workout plan using {equipment}.",
            "days": [
                {
                    "day_name": f"Day {i+1}",
                    "focus": "Full Body",
                    "exercises": [
                        {
                            "name": "Push-Up",
                            "sets": 3,
                            "reps": "10-15",
                            "rest_seconds": 60,
                            "instructions": "Start in a high plank. Lower chest to floor, push back up.",
                            "muscle_groups": ["chest", "triceps", "shoulders"],
                        }
                    ],
                }
                for i in range(days_per_week)
            ],
        }


async def generate_meal_plan(
    calories_target: int,
    protein_target: int,
    preferences: list,
) -> dict:
    """
    Generate a 7-day meal plan.

    Returns a dict with key:
      days[] → day, meals { breakfast, lunch, dinner, snack }
        → name, calories, protein, carbs, fat, ingredients[], instructions
    """
    prefs = ", ".join(preferences) if preferences else "no dietary restrictions"

    prompt = f"""Create a 7-day meal plan targeting {calories_target} calories/day and {protein_target}g protein/day.
Dietary preferences: {prefs}.

Return ONLY valid JSON with this structure:
{{
  "days": [
    {{
      "day": "Monday",
      "meals": {{
        "breakfast": {{
          "name": "...",
          "calories": 450,
          "protein": 35,
          "carbs": 40,
          "fat": 12,
          "ingredients": ["..."],
          "instructions": "..."
        }},
        "lunch": {{ ... }},
        "dinner": {{ ... }},
        "snack": {{ ... }}
      }},
      "daily_totals": {{
        "calories": {calories_target},
        "protein": {protein_target},
        "carbs": 200,
        "fat": 65
      }}
    }}
  ]
}}

Make meals practical, delicious, and easy to prepare. Vary the meals across the week."""

    try:
        response = await client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.8,
        )
        content = response.choices[0].message.content
        return json.loads(content)
    except Exception as e:
        logger.error(f"AI meal plan generation failed: {e}")
        return {
            "days": [
                {
                    "day": day,
                    "meals": {
                        "breakfast": {
                            "name": "Greek Yogurt with Berries",
                            "calories": 350,
                            "protein": 25,
                            "carbs": 35,
                            "fat": 8,
                            "ingredients": ["1 cup Greek yogurt", "1/2 cup mixed berries", "1 tbsp honey"],
                            "instructions": "Mix yogurt and berries. Drizzle honey on top.",
                        },
                        "lunch": {
                            "name": "Grilled Chicken Salad",
                            "calories": 500,
                            "protein": 45,
                            "carbs": 25,
                            "fat": 20,
                            "ingredients": ["6 oz chicken breast", "2 cups mixed greens", "olive oil", "lemon"],
                            "instructions": "Grill chicken, slice over greens, dress with olive oil and lemon.",
                        },
                        "dinner": {
                            "name": "Salmon with Roasted Vegetables",
                            "calories": 600,
                            "protein": 50,
                            "carbs": 40,
                            "fat": 22,
                            "ingredients": ["6 oz salmon fillet", "1 cup broccoli", "1 cup sweet potato"],
                            "instructions": "Roast veggies at 400°F for 25 min. Pan-sear salmon 4 min each side.",
                        },
                        "snack": {
                            "name": "Protein Shake with Almonds",
                            "calories": 300,
                            "protein": 30,
                            "carbs": 15,
                            "fat": 10,
                            "ingredients": ["1 scoop whey protein", "1 cup almond milk", "20 almonds"],
                            "instructions": "Blend protein with almond milk. Eat almonds on the side.",
                        },
                    },
                    "daily_totals": {
                        "calories": 1750,
                        "protein": 150,
                        "carbs": 115,
                        "fat": 60,
                    },
                }
                for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            ]
        }


async def adjust_plan_weekly(user_stats: dict, current_plan: dict) -> str:
    """
    Analyze a user's weekly performance and return 3 specific, actionable adjustments.

    user_stats: {
      workouts_completed, avg_calories, avg_protein, weight_change,
      energy_level (1-10), notes
    }
    current_plan: WorkoutPlan dict with at least 'plan_name'
    """
    prompt = f"""You are an expert personal trainer and nutritionist.

A user completed their week with these stats:
{json.dumps(user_stats, indent=2)}

Their current plan: {current_plan.get('plan_name', 'Custom Plan')}

Provide exactly 3 specific, actionable adjustments to improve their results next week.
Be direct, concrete, and motivating. Format as:

1. [ADJUSTMENT]: [specific action they should take]
2. [ADJUSTMENT]: [specific action they should take]  
3. [ADJUSTMENT]: [specific action they should take]

Then add a brief motivational close (1-2 sentences)."""

    try:
        response = await client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=400,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"AI weekly adjustment failed: {e}")
        return (
            "1. INTENSITY: Increase your working weight by 5% on compound lifts.\n"
            "2. NUTRITION: Hit your protein target within 10g every day — prep meals Sunday.\n"
            "3. RECOVERY: Add one extra rest day if energy is below 6/10 mid-week.\n\n"
            "Stay consistent — every rep and every meal compounds over time. Let's go!"
        )
