Rendering synthesizes missing annotation appearances under one budget for
every type it draws: it draws none of them when they would draw more than
262,144 path points in all, or when one annotation holds more than 65,536
points or 4,096 quadrilaterals, in place of the former budget of 4,096
text-markup quadrilaterals and 65,536 segments. An authored `/CA` that is no
number from 0 to 1 still leaves an annotation undrawn.
