# Panel status LEDs

The per-field status LEDs on M-DOT touch panels appear as switch entities. Commands reach them only when the integration signs in with an admin account.

To make an LED controllable, create its app object in Ampio Designer and leave the LED out of Designer logic. An LED bound to a Designer condition accepts the command, but the module's own logic re-asserts it within seconds.
