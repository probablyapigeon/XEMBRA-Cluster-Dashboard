// Mara v0.3 — XC Runtime v0.2 event + observation + persistent memory demo.
// These parameters are demonstration dynamics, not learned psychological laws.

xembra Mara version 0.4 {
  seed = 17701

  state Psi {
    trust: real = 0.55
    fear: real = 0.18
    loyalty: real = 0.46
    curiosity: real = 0.66
    resentment: real = 0.08
  }

  memory Episodic {
    capacity = 64
    decay = 0.12
    top_k = 4
    similarity_weight = 0.25
    salience_weight = 0.50
    recency_weight = 0.25
  }

  observation PlayerGift {
    vector = [ 0.18, -0.04,  0.12,  0.05, -0.10 ]
    kind = "affiliative"
    salience = 0.55
  }

  observation PlayerTalk {
    vector = [ 0.04, -0.02,  0.02,  0.08, -0.02 ]
    kind = "social"
    salience = 0.20
  }

  observation PlayerHit {
    vector = [ -0.38,  0.48, -0.24, -0.12,  0.50 ]
    kind = "hostile"
    salience = 0.95
  }

  observation PlayerIgnore {
    vector = [ -0.08,  0.03, -0.10, -0.05,  0.14 ]
    kind = "withdrawal"
    salience = 0.35
  }

  matrix F = [
    [ 1.02, -0.10,  0.05,  0.08, -0.03],
    [-0.02,  0.95, -0.04,  0.02,  0.05],
    [ 0.04, -0.03,  1.01,  0.05, -0.02],
    [ 0.03, -0.01,  0.02,  1.05, -0.02],
    [-0.02,  0.06, -0.02, -0.03,  0.98]
  ]

  matrix V = [
    [ 1.00, -0.08,  0.12,  0.04, -0.05],
    [-0.06,  1.02, -0.02, -0.03,  0.08],
    [ 0.10, -0.02,  1.03,  0.02, -0.05],
    [ 0.02,  0.01,  0.03,  0.98, -0.04],
    [-0.05,  0.10, -0.04, -0.02,  1.04]
  ]

  operator Focus {
    map = clamp(tanh(F @ Psi), 0, 1)
  }

  // Recency-weighted episodic memory is fed back as a small state bias.
  operator Recall {
    map = clamp(Psi + 0.16 * recall_vector("Episodic"), 0, 1)
  }

  operator Volition {
    map = clamp(tanh(V @ Psi), 0, 1)
  }

  measure DN {
    value = norm(Volition(Focus(Psi)) - Focus(Volition(Psi)))
  }

  measure hostile_memory {
    value = recall_signal("Episodic", "PlayerHit")
  }

  actions { HELP ASK_QUESTION REFUSE WITHDRAW }

  policy ActionPolicy {
    // Policy can inspect persistent history explicitly. These weights are demo parameters.
    score HELP = 1.20 * trust + loyalty + 0.20 * curiosity - 1.10 * fear - 0.80 * resentment - 1.50 * recall_signal("Episodic", "PlayerHit")
    score ASK_QUESTION = 0.80 * curiosity + 0.20 * trust - 0.30 * fear
    score REFUSE = 1.20 * resentment + 0.90 * fear - 0.70 * trust + 2.00 * recall_signal("Episodic", "PlayerHit")
    score WITHDRAW = 1.20 * fear + 0.40 * resentment - 0.40 * trust + 1.00 * recall_signal("Episodic", "PlayerHit")
    select argmax
  }

  cycle {
    Psi <- Focus(Psi)
    Psi <- Recall(Psi)
    Psi <- Volition(Psi)
    action <- ActionPolicy(Psi)
  }

  event PlayerGift {
    observe PlayerGift
    Psi <- clamp(Psi + Obs, 0, 1)
    remember Episodic
    cycle 1
  }

  event PlayerTalk {
    observe PlayerTalk
    Psi <- clamp(Psi + Obs, 0, 1)
    remember Episodic
    cycle 1
  }

  event PlayerHit {
    observe PlayerHit
    Psi <- clamp(Psi + Obs, 0, 1)
    remember Episodic
    cycle 1
  }

  event PlayerIgnore {
    observe PlayerIgnore
    Psi <- clamp(Psi + Obs, 0, 1)
    remember Episodic
    cycle 1
  }
}
