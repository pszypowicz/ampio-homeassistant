# Devices, areas, and entity ids

This page explains how the integration builds its devices, and where the names and the areas come from. It also explains where an entity id comes from, and what a change in Ampio Designer does.

## The device tree

The integration builds three levels of devices:

- One hub device for the M-SERV.
- One device per Ampio module, under the hub.
- One device per Ampio object, under its module.

The M-SERV's own objects sit under the hub. The tree follows the module each object belongs to in Ampio Designer.

One physical output can carry several objects in Ampio Designer. Each object gets its own entity.

One M-SERV per Home Assistant. Object ids are unique per server only, so the integration allows one entry.

## Names

An object device takes the name you gave the object in the Ampio app. A module takes the name you gave it in Ampio Designer. When your account is not an administrator one, a module reads `Ampio module <MAC>` instead, for example `Ampio module 0xCB8F`, with the address written in hex the way Designer's MAC field shows it. A module whose Designer device row you deleted reads the same on an administrator account, because no row is left to name it. The hub is always `M-SERV`.

A rename in Designer or in the app does not reach Home Assistant straight away. The integration watches the catalogue fields that decide which entities exist and which module they hang under, and a name is none of them, so a save that changes only a name leaves the devices as they are until they register again, on a reload or a restart. A name you typed yourself in Home Assistant wins over whatever arrives then, so rename the device here when you want the name to stay put. Entity ids are settled at first registration either way, so neither rename moves one.

## The Identify button

On the administrator login, each module device has an Identify button. A press lights the module's CAN LED for 30 s, so you can find the module in the cabinet. A standard account is given no Identify button, because the Ampio server would not carry the frame. A DIN-rail module lights its CAN LED steadily. An M-DOT panel lights the LED on its back only, so a wall-mounted panel gives no visible sign.

The module keeps the LED lit until it receives a stop. The integration sends the stop after 30 s. If you reload or remove the integration before then, it sends the stop at that moment instead. If Home Assistant restarts during those 30 s, the stop is never sent. Then the LED stays lit until Ampio Designer sends a stop or the module restarts.

## The module sensors

On the administrator login, each module device also carries two diagnostic sensors, a supply voltage and a temperature, both readings the module reports about itself. A standard account is given neither, for the same reason as the Identify button. A module that never sends that reading leaves its sensor unknown rather than unavailable, because the reading is not replayed at connect and silence says nothing about the module's health.

## The buzzer

A module with a touch panel also gets a siren entity for its buzzer, on the administrator login alone. Turning it on sounds a single tone for a single length. The `ampio.buzz_pattern` action reaches the two-tone sequences the buzzer also supports, described in the Actions section of the project README.

## The touch lock

A module with a touch panel also gets an Unlock touch button, on the administrator login alone. A press releases the panel's touch lock. The `ampio.lock_touch` action sets the lock, described in the Actions section of the project README. A lock always expires and caps at 655.35 seconds, and nothing on the bus reports whether a panel is locked. A person can also set or clear the lock at the panel itself, with its own touch field combination.

## The panel colors

A module with a touch panel gets two more light entities, on the administrator login alone and only when the panel itself reports the capability. A Backlight holds the resting color of its touch field icons, and a Status light holds the color its indicators show. The status light is what reacts when a field is touched or the object behind it turns on, so it carries its own color separately from the backlight's.

Both are runtime overrides, not Designer settings. A panel restart restores whatever Ampio Designer stored for it, undoing anything either entity asked for. Neither reads back either, because nothing on the bus reports a panel's current color, so each entity shows what was last asked for and starts from the stored Designer default. The `ampio.set_backlight_fields` and `ampio.set_status_fields` actions color individual touch fields rather than the whole surface, described in the Actions section of the project README.

## Areas

An object device takes the object's app room as its area when Home Assistant creates it. After that the area is yours. The integration never moves a device.

Home Assistant matches rooms to areas by name. If your Ampio rooms and your areas differ in spelling, rename one side before you add the integration, or move the devices afterwards.

## Entity ids

Home Assistant builds an entity id when it first registers the entity, and the id holds still after that. By default it takes the area name, the device name and the entity name, in that order, and it leaves out any part that is empty, which is why an object's main entity, whose own name is empty, reads as the area and the device name alone. A rename in Ampio Designer or in Home Assistant changes the name you see and leaves the id alone, so your automations keep working. An object with no name in Designer takes a numbered placeholder instead.

The Entity ID format setting under Settings, then System, can leave out the area or add the floor, and it cannot leave out the device name or the entity name. Ampio entities follow it like any other integration's. An object called Taras LED in the room Taras reads `light.taras_taras_led`. A module called M-SENS Salon gives its Identify button `button.m_sens_salon_identify`, with no area in front of it, because the integration seeds no area on a module device. With the default format, if you give a module device an area, an id on it that registers or that you regenerate after that carries the area in front.

Two objects that carry the same name in Ampio Designer and share a room, or share having none, cannot share an id, so one of the two takes Home Assistant's `_2` suffix and reads `button.dzwonek_2` beside `button.dzwonek`. The same two names in different rooms read `button.salon_dzwonek` and `button.kuchnia_dzwonek`, and nothing collides. Give a colliding pair distinct names in Designer if you want to tell them apart by their ids.

An install from an earlier release keeps the ids it already has, because an id is stored against the entity's unique id, and a release changes those only where the release note says it does. To rebuild one from the names you have now, open the entity, select the cog icon, and use Home Assistant's control for regenerating an entity id. It works on an entity from an earlier release as well as on a fresh one.

Neither an Ampio account tier change nor an M-SERV replacement moves an id.

See [faq.md](faq.md) for what an update does to an entity id, and for the reset procedure.

## Changes in Ampio Designer

The integration follows the Ampio catalogue while it runs, on both account tiers. An object you add in Designer gets its entity within seconds, under its module, in its app room. On a standard account the object must also be granted to the Home Assistant user in the app. An object you delete or hide loses its entity at once, and the repair on the Settings page lists it. The delete stays yours, because on a standard account a lost app permission looks the same as a delete. A relay you re-tag as a light, or a pulse time you set, is followed the same way.

The M-SERV pushes the object tables to both account tiers when you save in Designer, so objects follow a save the same way on either login. It never pushes the module list, which the administrator login alone receives. The administrator login re-reads the module list when the digest of the app tables changes on a save, so the module list follows a Designer save a few seconds later.

## Moving an object to another module

If you move an object to another module in Designer, the integration removes the object's entities. The repair on the Settings page then offers the delete of its device. After the delete, the object comes back under the new module with its area and its name. Replacing a module is not a move, because the replacement takes the same MAC address and the object stays where it was. See [designer-quirks.md](designer-quirks.md) for the reason.

## The Matter checkbox

Do not clear an object's Matter checkbox in Designer once the object has an entity here. Unchecking it clears the object's leaf id, which is the pointer this integration drives the object through, so the integration stops building the object's entities until you check the box again. The records stay where they are, the object's device included, so an entity that comes back as the same type comes back with the id, the name and the area it had. A repair on the Settings page names the objects this happened to. To stop the M-SERV's Matter bridge, use "Clear configuration" in Designer's Matter panel instead. See [designer-quirks.md](designer-quirks.md).
