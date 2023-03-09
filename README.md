# hbot-washer
Wash trader script for humming bot

# Hummingbot Setup

Please read the official docs on how to setup hummingbot.

[https://docs.hummingbot.org/](https://docs.hummingbot.org/)

# Running The Script
Once you have Hummingbot setup, copy ``washer.py`` to the scripts directory.

Then start hummingbot and run the script you just copied using

```
start --script washer.py
```

## Updating Paper Balances
If paper trading, balances can be updated by running the following commands with the coins of your choice. Eg if trading TRX/USDT pair then

```
balance paper TRX [amount]
balance paper USDT [amount]
```

## Changing script variables
If you want to change any of the variables in the script you have to stop hummingbot first
```
stop
```

Now you can edit any of the script files then restart

```
start --script washer.py
```
