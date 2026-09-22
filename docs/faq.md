# Frequently asked questions

If something looks wrong after an update or after a change in Ampio Designer, find the symptom below. Each answer tells you how to check whether it affects you, and how to fix it.

## Back up first

Do not start a procedure on this page without a backup. Some of them delete records that Home Assistant cannot rebuild from the Ampio server.

1. Open Settings, then System, then Backups.
2. Select "Create backup".
3. Wait for the backup to finish.

## Entities are missing or stay unavailable after an update

**Check:** Open Settings, then Devices and services, then Ampio. Compare the entity count with what you had. An entity with the state `unavailable` or the label "restored" is one the integration no longer builds.

**Fix:** Remove the Ampio integration entry, then add it again. Home Assistant remembers a removed entity for 30 days, so the re-add restores your entity ids, your renames, and your areas. It is the supported first step here, so reach for it early. If the entity count is still wrong afterwards, download the diagnostics as described in [debugging.md](debugging.md) and report it in the issues.

## My module devices lost their names and areas after an update

**Check:** Open Settings, then Devices and services, then Ampio. Every module device is listed under the name the Ampio server gives it, with no area and none of the labels you put on it, and the entities on it have new entity ids and no history. A repair on the Settings page lists leftover Ampio records, and you recognize the module devices it names as the ones you had. Your object entities are missing at the same time.

This integration ships as a beta and carries no upgrade path. Every version is prepared as though it were a fresh install, and a release that changes what a device record is keyed on builds those devices again under the new key. Home Assistant treats a device under a new key as a new device, so the name you typed, the area, the labels, and the history of the entities on it all start empty. Each object's device still hangs under one of the old module devices, which is a parent its object no longer belongs to, and Home Assistant cannot move a child device to another parent, so the integration holds those objects' entities back until the old devices go.

That covers every object on a module, which on a normal install is most of the entities this integration provides. Only the M-SERV's own objects carry on through it, because their devices hang under the hub rather than under a module.

**Fix:** Submit that repair. It deletes the old module devices, and the reload behind it puts every object back under its module within seconds, with the object's entity ids, its own name, and its own area intact. Then give each module device its name, its area, and its labels again.

Take a note of the module entity ids your automations use before you submit, because the administrator-only module entities come back under new ids: the Identify button, the supply voltage and temperature sensors, the buzzer, the Unlock touch button, and the Backlight and Status light. Their old records are among what the repair deletes.

An object device with no area of its own inherits its module's area, so an object you never placed yourself reads as having none until you give its module an area again. An object device you did put in an area keeps it.

## Setup does not finish, and stays on "Retrying setup"

**Check:** Open Settings, then Devices and services. The Ampio entry shows "Retrying setup" instead of its usual state, and the reason underneath reads "Connected to the Ampio server, but it did not answer the module description request." This affects the administrator login only, because a standard account never sends that request.

**Fix:** None is required. The integration retries the request on its own, waiting longer between each attempt, and finishes loading as soon as the M-SERV answers. If it stays stuck for several minutes, check whether the M-SERV itself is slow to respond, mid-restart, or overloaded, and give it time to recover. If the entry still will not finish after that, download the diagnostics as described in [debugging.md](debugging.md) and report it in the issues.

## An entity is listed that Ampio Designer no longer has

**Check:** Such an entity shows the state `unavailable` or the label "restored". After each start or reload the integration raises up to three repairs on the Settings page, under Repairs. One lists the devices and entities it did not build and cannot explain. One lists the entities it withheld because your Ampio account is not the administrator one. The third names Ampio Designer rows it could not use at all, and it asks you to fix them in Designer rather than offering to delete anything. Read that one first, because an entity it accounts for is not stale. [designer-quirks.md](designer-quirks.md) covers both faults it reports.

**Fix:** Select Submit on the repair that lists the records to clean up, and it deletes them all at once. The integration then reloads. An object that you moved to another module in Ampio Designer comes back under its new module.

The repair never deletes on its own. On an account that is not the administrator one, an object that lost its app permission looks the same as a deleted object. Read the list before you submit.

To delete a single record by hand instead:

1. Open Settings, then Devices and services, then Entities.
2. Search for the entity.
3. Select it, then select the cog icon.
4. Select "Delete".

If the "Delete" button is not offered, the integration still creates that entity. Check Ampio Designer before you go further.

## I changed my Ampio password

Home Assistant notices on its own. The entry stops, and a notification asks you to sign in again. Open it, enter the new password, and submit.

To change the password before it breaks, open the entry, choose Reconfigure from its menu, and enter the new one.

## I want to use a different Ampio account, or I replaced my M-SERV

Open the entry, choose Reconfigure from its menu, and enter the address and the credentials to use. Your devices and your entities keep their ids, their areas, and any name you gave them yourself.

For a change of account, address or hardware, Reconfigure is the way to do it without removing the entry. A delete removes every device record and every entity record. Home Assistant remembers both for 30 days, so an add inside that window brings back the renames, the areas and the labels of everything whose identity still matches. Anything the Ampio server no longer serves under the identity it had does not come back, and after 30 days none of it does.

A different account changes what the server serves you. An app-created user receives the objects granted to it in the Ampio app, so an object outside that grant loses its entities. The repair on the Settings page lists them. Read the list before you submit it. If you move to a standard account, a second repair lists the administrator-only entities it withheld, such as the Identify buttons, the module sensors, the buzzer, the Unlock touch buttons, and the Backlight and Status light entities, and that one is explained under "The administrator-only entities are gone".

If the address you enter answers with different M-SERV hardware, the flow asks you to confirm first. It names the CAN address it found. Continue only if you replaced the M-SERV, or if you meant to point Home Assistant at another one.

## My entity ids look different from the ones in the docs

**Check:** Open Settings, then Devices and services, then Entities, and filter the list by the Ampio integration. Home Assistant composes each id from the area name, the device name and the entity name, so an object called Taras LED in the room Taras reads `light.taras_taras_led`, and the Identify button on a module called M-SENS Salon reads `button.m_sens_salon_identify`. The Entity ID format setting under Settings, then System, decides which of those parts take part. An install from an earlier release keeps the ids that release gave it, because an id is stored against the entity's unique id, and a release changes those only where the release note says it does.

**Fix:** None is required. An id that works goes on working, and nothing forces you to change it. To rebuild one from the names you have now, open the entity, select the cog icon, and use Home Assistant's control for regenerating an entity id. To rebuild every Ampio id at once, use the reset procedure below. It is a support procedure, and no update requires it.

## Why does an entity id stay the same when I rename something?

Home Assistant builds an entity id once, when the entity registers for the first time, and stores it from then on. A device rename does not move it. An area change does not move it. An update does not move it either, as long as the release keeps what the entity is keyed on underneath. A release that changes that key builds the entity again, with a new id and no history, and the release note says so. Short of that, the only thing that rebuilds an id is you, through the control for regenerating an entity id on the entity's own settings page.

Removing the integration does not move it either. Home Assistant remembers a removed entity for 30 days. If you add the integration again inside that window, the old entity id comes back. The name you gave the entity and the area you put it in come back with it.

Your automations therefore keep working across a rename, a reinstall, and every update the release note does not warn you about.

## A relay I tagged as a light shows as a switch

See [designer-quirks.md](designer-quirks.md). That page tells you how to check the tag in the diagnostics, and how to re-save the output in Designer.

## An object sits under the wrong module

See [designer-quirks.md](designer-quirks.md). That page explains why Home Assistant cannot move a child device, and how the repair puts the object under its new module.

## A cover's arrow is missing, every control is gone, or an automation that names it fails

**Check:** On the dashboard, the cover card offers only the arrow for the direction that still works and drops the other one, with no toast and no notification explaining why, or the card offers no control at all and reports only the position. An automation that names the cover by its entity id fails instead, with a message such as "Entity cover.sypialnia_roleta_sypialnia does not support action cover.open_cover."

A rule in Ampio Designer, or a lock the `ampio.set_roller_lock` action set, is locking the cover's travel. A Designer rule holds the lock through one of the roller actions, "Disable movement", "Disable closing", or "Disable opening", for as long as its trigger holds, so a wind alarm or a fire alarm can hold it for a long time, and the lock clears on its own once the trigger clears. A lock the action set holds until something releases it. The cover's Opening lock and Closing lock diagnostic binary sensors report which direction, if any, is currently held.

The module drops the blocked command with no error and no reply, so the integration removes the control instead of letting a press do nothing. The cover keeps reporting its position the whole time. The position slider still works in the direction that is not locked, and refuses the other with a message naming the `Ampio: Set roller lock` action and the Designer rule that can hold it. `cover.toggle` needs both directions, so locking either one disables it entirely.

The lock covers the slats as well. On a blind, the tilt arrows and the tilt slider follow the same two directions as the travel, so a lock on opening also stops a slat turn toward open and leaves a turn toward closed working.

A third cause takes every control at once, arrows, slider, and both stop buttons together, and the tell is exactly that completeness. A lock in both directions still leaves the stop buttons working, because a stop is not a move and the module never blocks it. If the stops are gone too, the object is marked read-only in Ampio Designer, and the server refuses every verb behind it, on every axis, so there is nothing left for this integration to offer.

An administrator can still hold or release this cover's roller lock through the `ampio.set_roller_lock` action while it is read-only, because the lock write rides the raw tree rather than the path the read-only marker gates. Doing so changes nothing about the cover itself. Only the lock binary sensors' own state moves, since the read-only refusal already applies above where the lock bits are read. A scene that captured this cover before it went read-only stops restoring it, silently, with no warning and no error.

**Fix:** For a lock, none is required. The arrow returns once the lock clears, whether that is a Designer rule's trigger or a call to `ampio.set_roller_lock` releasing it. For read-only, clear the checkbox in Ampio Designer if you want the cover to take commands again. An automation that targets the cover through an area, a device, or a label skips it silently while either cause holds. An automation that names the cover by its entity id raises and halts the rest of the sequence unless the action sets `continue_on_error: true`.

## A cover's roller lock is unknown, or the set roller lock action fails

**Check:** Read the Opening lock and Closing lock binary sensors under the cover device's Diagnostic section. Both account tiers receive these readings. Before the module reports its lock bits, both sensors read `unknown`. A reading of `off` means that the module reported that direction as released.

**Fix:** If the action requires the administrator account, ask the administrator to hold or release the lock. A standard account can read the lock sensors but cannot change the lock.

If the message says that the module does not support the roller lock, that module cannot perform this action. An older module generation can accept ordinary cover commands without supporting lock commands. There is no integration setting that enables this hardware function.

If the module did not answer during setup, make sure that the module is online. Reload the Ampio integration to request its descriptions again. The module must answer before the action can send a lock command.

If no unique module row matches the cover, open Ampio Designer. Restore the missing module row or give each device its own MAC address. Save the changes.

If the Ampio server no longer lists the cover, restore it in Ampio Designer. Alternatively, select an existing cover for this action.

A lock the action sets never expires on its own. Whichever account sets one owns releasing it: call the action again with `blocked` off, or clear it from wherever it was set, such as an automation. A Designer wind or fire alarm rule clears its lock when its trigger clears, and does not re-assert one you released underneath it, so releasing that lock with the action leaves the cover free to move until the rule fires again.

## A warm/cold white light's color temperature does not match my strip

The slider works, and the numbers are a scale rather than a measurement.

Ampio serves a warm/cold white output as two raw bytes, a power level and a coldness level, each 0 to 255. The M-SERV publishes no kelvin range for such an object, and its `min` and `max` columns read 0 and 255. So nothing on the wire says what white 3512 K is.

The integration maps the coldness byte onto Home Assistant's own default range, 2000 K at the warmest end and 6535 K at the coldest. The ends of the slider reach the ends of your strip. A number in the middle is a position on that scale rather than a measured temperature, so a strip sold as 2700 K to 6000 K shows numbers that run wider than its specification.

Every position on the slider is stable. The same number always gives the same white, and an automation or a scene that stores one reproduces it.

## My analog flag ignores the turn-on time I set in Designer

Ampio Designer offers a turn-on time on an analog flag, the same column its editor offers on a relay, a flag, a dimmer, and the RGB kinds. This repo has measured the pulse itself only on a relay and a flag. Home Assistant does not send it here, because the M-SERV does not apply it to this object type. A measurement against a live M-SERV, on 2026-09-15, wrote a value to an analog flag with that time attached, and the flag still held the written value fifty-three seconds later, with no reversion. See [designer-quirks.md](designer-quirks.md) for the numbers behind it.

The value you write to the number entity stays where you wrote it until something writes another one. If you want a value to fall back after a delay, build that timing in an automation, because Designer's turn-on time field does nothing for this object type.

## A read-only object still shows a Pulse time reading

**Check:** Open Settings, then Devices and services, then Entities, and find the object's Pulse time diagnostic sensor. If the switch, button, or light beside it refuses every command with an error naming the read-only marker, open the object in Ampio Designer and confirm its read-only checkbox is set.

Ampio Designer's read-only checkbox blocks a write to the object at the server, on both account tiers, not only from Home Assistant. A relay, a flag, or a light marked read-only keeps its Pulse time diagnostic sensor, and the sensor keeps reporting whatever turn-on time Designer stores for it, even though the server drops every write to the object and that time never actually times anything. The reading is accurate, and it reports the time a turn-on write would carry if the checkbox were cleared.

**Fix:** None is required. Clear the read-only checkbox in Ampio Designer to let the object accept writes again. The pulse time then applies to the next turn-on the same as it would for any object with a Designer time.

## A module shows no last-seen time in the diagnostics

**Check:** Find the module's row under `snapshot.modules` in the diagnostics download, as described in [debugging.md](debugging.md). Look at `supply_voltage` in the same row. If that value is empty too, the module sends no health broadcast, and its last-seen time moves only when one of its objects changes. On the reference install the roller modules, the M-SERV row, and an M-CON-s on old firmware send none. The library lists every type and firmware in [raw-channel-bridge.md](https://github.com/pszypowicz/ampio-mqtt/blob/main/docs/raw-channel-bridge.md). A module that shows a voltage does send the broadcast. Its last-seen time can still lag by a few minutes after a restart, because the broadcast comes on a change of the reading only.

**Fix:** None is required. An empty value says only that nothing arrived from that module since the restart. Move one of its covers, or wait for one of its objects to change, and the row fills. If a light or a cover on that module still responds, the module is alive.

## My touch panels go dark when I turn off all the lights

**Check:** Read the automation or the script that turns the lights off. Two forms of it reach a panel's Backlight and Status light:

- A template over `states.light` whose result is passed as `entity_id`.
- A call to `light.turn_off` with `entity_id: all`.

Every other way to target lights skips them already. Both entities carry the diagnostic category, and Home Assistant leaves an entity with a category out of area, device, and floor targeting, out of the voice assistants, and out of the HomeKit bridge.

The two forms above are different. A template over `states.light` sees every light entity, and no template test reports an entity's category. A list of entity ids is a direct target, and a direct target is never filtered.

**Fix:** Reject the panel entities inside the template, and use a label to find them. A panel light's id ends in the entity's own name, which reads in your Home Assistant language, so the pattern to write differs from one install to the next. Nothing else in the id marks the light as this integration's either, so a pattern over ids can catch another integration's backlight and miss yours. A label is something you put on the entities yourself, and it holds through a rename and through a regenerated id.

Open Settings, then Areas and labels, and create a label for them. Put it on the Backlight and the Status light of every panel. Then name the label in the template, either by its name or by the id Home Assistant gave it:

```jinja
{%- set skip = label_entities('podswietlenie_paneli') -%}
{{ states.light
   | rejectattr('entity_id', 'in', skip)
   | map(attribute='entity_id') | list }}
```

Each panel you add later needs the label on its two entities too.

For `entity_id: all`, name your targets instead. Home Assistant turns off every light entity for that value, and the category changes nothing.

The same reject belongs in every template that reads `states.light`. A sensor that counts the lights that are on, and a card that lists them, both include two entities per panel without it.

## The administrator-only entities are gone

Several module entities are provided with the administrator login alone: the Identify button, the supply voltage and temperature sensors, the buzzer on a module with a touch panel, that panel's Unlock touch button, and its Backlight and Status light. The Ampio server carries the frames and the readings behind them to that login and no other, so a standard account is given none of them.

The Identify button once existed on both accounts, where a press on a standard account only ever returned an error. If you did not change anything, an update is what removed it. If you changed the account this integration uses, that removed it too. The sensors, the buzzer, the Unlock touch button, and the Backlight and Status light are administrator-only from their first release, so a standard account never had them to lose.

A repair on the Settings page lists whichever of these entities your account withholds. Submit it to delete the records, or leave it alone. If you point the integration back at the administrator account, the entities come back on their own with the same entity ids.

## My module devices are named "Ampio module 0xCB8F"

You see this on a standard Ampio account most of the time. The names you gave your modules in Ampio Designer are served to the administrator login alone, so a module falls back to its address on the Ampio bus, written in hex the way Designer's MAC field shows it. It is the same device and the same module either way. On an administrator account a module reads this way when you deleted its device row in Designer and left its objects behind, because no row is left to name it. With the device row in place, an administrator account shows the Designer name.

If the names you typed yourself are gone as well, along with your areas and your labels, then a release changed what a module device is keyed on and built those devices again. "My module devices lost their names and areas after an update" above covers that case and the repair that finishes it.

## How do I reset every Ampio entity id?

Use this to rebuild every Ampio entity id at once. It is a destructive reset of your entity preferences. The command deletes every Ampio entity record, the live ones and the ones Home Assistant is remembering alike, so the entity names you typed, the areas you gave entities of their own, the labels you put on them, and their per-entity settings go with them. Home Assistant then builds the ids again on the next start, from the device names that survive and from the entity names this integration supplies.

Your devices keep everything, so their names, their areas and their labels come through untouched. Put your entity labels back afterwards, including the panel labels described under "My touch panels go dark when I turn off all the lights", because the template there stops finding anything without them.

For a single entity, the control for regenerating an entity id on its settings page rebuilds that one id and touches nothing else.

**Every automation, script, scene, and dashboard card that names an Ampio entity id stops working.** Write those ids down first, and plan to repoint them.

You need shell access to the Home Assistant host, through the SSH add-on or the Terminal add-on.

1. Take a backup, as described above.
2. Write down the Ampio entity ids your automations use.
3. Stop Home Assistant:

   ```sh
   ha core stop
   ```

4. Copy the entity registry, then remove every Ampio record from it:

   ```sh
   sudo cp /config/.storage/core.entity_registry /config/.storage/core.entity_registry.bak
   sudo jq '(.data.entities, .data.deleted_entities) |= map(select(.platform != "ampio"))' \
     /config/.storage/core.entity_registry > /tmp/registry.json
   sudo cp /tmp/registry.json /config/.storage/core.entity_registry
   ```

5. Start Home Assistant:

   ```sh
   ha core start
   ```

6. Open Settings, then Devices and services, then Ampio. Confirm that the entity count matches what you had.
7. Repoint your automations at the new ids.
8. Put your entity labels back, the panel labels among them, and give back any entity name and any entity area you had set yourself.

The `deleted_entities` list matters as much as the `entities` list. Leave the deleted records in place, and Home Assistant restores every old id on the next start.

### If something goes wrong

Stop Home Assistant, copy the backup file back, then start Home Assistant:

```sh
ha core stop
sudo cp /config/.storage/core.entity_registry.bak /config/.storage/core.entity_registry
ha core start
```

If the instance does not start at all, restore the full backup from Settings, System, Backups.

## What this does not touch

- Your Ampio configuration. The M-SERV holds it, and this integration only reads it.
- Your devices. Device names and areas live in a separate registry.
- Any other integration. The filter selects the `ampio` platform alone.
