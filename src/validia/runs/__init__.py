"""Running a suite against a model: reaching the provider, sending every case, grading the replies.

``access`` names the model and finds its API key; ``runner`` builds franca's model, sends
each trial, retries what is worth retrying, grades the reply and sums the run up. Loop-
neutral: scheduling trials belongs to the caller.
"""
